"""One environment, not the whole host: what a project folder declares (D85, D87).

A host scan finds every engine and every model it serves, but an agent only
when it is running and identifies itself on a port (D63). An operator who
wants one deployment audited, out of several on the same machine, names its
folder instead: `discover scan --project DIR`.

**The whole folder, read for declarations only** (D87). OPAAS walks the folder
the operator named, bounded (`MAX_DEPTH`, `MAX_FILES`, `MAX_FILE_BYTES`) and
skipping what is installed or generated (`_SKIP_DIRS`), and reads each file
for what it declares about the agent stack:

- MCP configurations and skill directories, read as `--config` and `--skills`
  would read them;
- agents: the graphs of a `langgraph.json` and every `StateGraph` built in
  code, an A2A agent card, the n8n or Flowise exports with an agent node, the
  `agents:` of an application's configuration (YAML or JSON), Claude Code
  subagents (`.claude/agents/*.md`), Claude Code in CI (a workflow using
  `anthropics/claude-code-action`) and GitHub Copilot's instructions;
- models: those the configuration and the exports name (with the provider
  section they sit in), and the quoted names in code that an engine here
  serves or that name a cloud model by its family;
- the frameworks the code imports, and the compose services with their images.

Configuration is parsed, never executed: YAML with `safe_load`, JSON with
`json`. Code is searched for imports, `StateGraph(` and quoted model names,
never run and never interpreted further: what a program calls at run time is
not read off its text, so the scan says what the text *names*.

The host is still observed, and the inventory keeps what it saw: the scope
says which of it belongs to the environment (`project.agents[].running`,
`project.models[].servedBy`). An agent the folder declares that is not
running is listed as such, never given an endpoint by guess.

**Publishable (invariant 12).** The folder is recorded by its name, files by
their path relative to it. Kept: names (agents, models, services, workflows),
images, framework names and counts. Never prompts, descriptions,
credentials, environment values or URLs beyond scheme, host and port.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opaas.core.hashing import sha256_file, sha256_json

#: How far, and how much, the walk reads.
MAX_DEPTH = 8
MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
#: Installed, generated or cached: never the project's own declarations.
_SKIP_DIRS = {"node_modules", ".venv", "venv", "env", "__pycache__", "site-packages", "dist",
              "build", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt",
              "coverage", "target", "vendor", ".idea", ".git", ".hg", ".svn"}
#: Hidden folders that hold agent configuration; every other hidden folder is skipped.
_HIDDEN_KEPT = {".claude", ".github", ".vscode", ".cursor", ".roo", ".gemini", ".well-known",
                ".continue", ".codeium"}

#: MCP configurations a project keeps, by client.
_MCP_CONFIGS = (".mcp.json", "mcp.json", ".vscode/mcp.json", ".cursor/mcp.json",
                ".roo/mcp.json", ".gemini/settings.json")
#: Skill directories a project keeps.
_SKILL_DIRS = (".claude/skills", "skills")
#: A2A agent cards, by their names.
_AGENT_CARDS = ("agent-card.json", "agent.json")
#: Exported workflows (n8n, Flowise) are looked for in these folders and the root.
_EXPORT_DIRS = {"", "workflows", "chatflows", "flows", "n8n", "flowise", "agents"}
#: Application configuration, by name: the agents and models it sets.
_CONFIG_NAME = re.compile(r"^(config|settings|agents?|models?|llm|app)([._-][\w.-]*)?"
                          r"\.(ya?ml|json)$", re.I)
_COMPOSE_NAME = re.compile(r"^(docker-)?compose([._-][\w.-]*)?\.ya?ml$", re.I)
_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}

#: A key naming a model, and the sections that say which provider serves it.
_MODEL_KEY = re.compile(r"^(model|model_name|modelName|[a-z_]*_model)$")
_PROVIDERS = {"ollama", "openai", "anthropic", "vllm", "azure", "azure_openai", "google",
              "gemini", "vertex", "mistral", "groq", "bedrock", "cohere", "lmstudio"}
#: Cloud model families, by name: a quoted name in code counts when it is one of these.
_CLOUD_FAMILIES = (("claude-", "anthropic"), ("gpt-", "openai"), ("o1", "openai"),
                   ("o3", "openai"), ("o4", "openai"), ("text-embedding-", "openai"),
                   ("gemini-", "google"))
#: Imports that name an agent or model framework, as `import`/`from`/`require` show them.
_FRAMEWORKS = {
    "langgraph": "LangGraph", "langchain": "LangChain", "crewai": "CrewAI",
    "autogen": "AutoGen", "llama_index": "LlamaIndex", "anthropic": "Anthropic SDK",
    "claude_agent_sdk": "Claude Agent SDK", "openai": "OpenAI SDK", "ollama": "Ollama SDK",
    "mcp": "MCP SDK", "smolagents": "smolagents", "pydantic_ai": "Pydantic AI",
    "@langchain/langgraph": "LangGraph", "@langchain/": "LangChain",
    "@anthropic-ai/sdk": "Anthropic SDK", "@anthropic-ai/claude-agent-sdk": "Claude Agent SDK",
    "@modelcontextprotocol/sdk": "MCP SDK", "openai/": "OpenAI SDK",
}
_PY_IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", re.M)
_JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*)["']([@\w./-]+)["']""")
_QUOTED = re.compile(r"""["']([A-Za-z0-9][\w.-]{1,60}(?::[\w.-]{1,40})?)["']""")
#: Images whose service is an engine or an agent framework.
_ENGINE_IMAGES = {"ollama/ollama": "ollama", "vllm/vllm-openai": "vllm"}
_AGENT_IMAGES = {"n8nio/n8n": "n8n", "flowiseai/flowise": "flowise",
                 "langchain/langgraph-api": "langgraph", "ghcr.io/open-webui/open-webui":
                 "open-webui", "langgenius/dify-api": "dify"}
#: Agent kinds smith can drive, once running; everything else is declared only.
_DRIVEN = {"langgraph", "a2a", "n8n", "flowise", "open-webui", "dify"}
#: A JSON export larger than this is not an agent declaration: not read.
MAX_EXPORT_BYTES = 2_000_000

_N8N_MODEL_NODES = ("lmchatollama", "lmollama", "lmchatopenai", "lmopenai", "lmchatanthropic")
_FLOWISE_MODEL_NODES = ("chatollama", "ollama", "chatopenai", "chatlocalai", "chatopenaicustom",
                        "chatanthropic")


class ProjectError(ValueError):
    """A project folder that cannot be scanned, with what to do instead."""


class _Found:
    """What the walk has found so far."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, str] = {}
        self.configs: list[Path] = []
        self.skill_dirs: list[Path] = []
        self.agents: list[dict[str, Any]] = []
        self.models: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, set[str]] = {}
        self.frameworks: dict[str, dict[str, Any]] = {}
        self.services: list[dict[str, Any]] = []
        self.not_read: list[dict[str, str]] = []

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read(self, path: Path) -> None:
        self.files.setdefault(self.rel(path), "sha256:" + sha256_file(path))

    def agent(self, path: Path, kind: str, name: str, **extra: Any) -> None:
        """One agent per kind and name: a second file declaring it is listed beside the first."""
        self.read(path)
        for agent in self.agents:
            if agent["kind"] == kind and agent["name"] == str(name):
                agent.setdefault("alsoIn", []).append(self.rel(path))
                for key, value in extra.items():
                    if value is not None:
                        agent.setdefault(key, value)
                return
        adapter = kind if kind in _DRIVEN else "declared"
        self.agents.append({"kind": kind, "adapter": adapter, "name": str(name),
                            "file": self.rel(path),
                            **{k: v for k, v in extra.items() if v is not None}})

    def model(self, path: Path, name: Any, provider: str | None = None) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            return
        self.read(path)
        entry = self.models.setdefault(name.strip(), {"name": name.strip(), "files": []})
        if provider and not entry.get("provider"):
            entry["provider"] = provider
        if self.rel(path) not in entry["files"]:
            entry["files"].append(self.rel(path))


def scan_project(root: Path) -> dict[str, Any]:
    """What `root` declares: MCP configs and skill dirs to read, agents and models it names.

    Returns the publishable scope (`root`, `files`, `agents`, `models`,
    `frameworks`, `services`, `notRead`) and, under `paths`, what `discover`
    reads next and the code's quoted names, matched against the host by
    `scope`. The caller drops `paths` before anything is written.
    """
    root = root.expanduser()
    if not root.is_dir():
        raise ProjectError(f"{root} is not a folder: name the folder of the environment to audit.")
    found = _Found(root.resolve())
    root = found.root
    for relative in _SKILL_DIRS:
        if (root / relative).is_dir():
            found.skill_dirs.append(root / relative)
    count = 0
    for path in _walk(root):
        count += 1
        if count > MAX_FILES:
            found.not_read.append({"path": "…", "reason": f"The walk stops at {MAX_FILES} files: "
                                   "name a narrower folder to read the rest."})
            break
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        _classify(found, path)
    agents = sorted(found.agents, key=lambda a: (a["kind"], a["name"], a["file"]))
    return {
        "root": root.name,
        "files": [{"path": p, "hash": h} for p, h in sorted(found.files.items())],
        "agents": agents,
        "models": sorted(found.models.values(), key=lambda m: m["name"]),
        "frameworks": sorted(found.frameworks.values(), key=lambda f: f["name"]),
        "services": sorted(found.services, key=lambda s: (s["file"], s["name"])),
        "notRead": [*found.not_read, {
            "path": "code at run time", "reason": "Code is searched for imports, graphs and "
            "quoted model names, never run: a model or agent chosen at run time is not seen."}],
        "paths": {"configs": found.configs, "skillDirs": found.skill_dirs,
                  "codeModels": {n: sorted(f) for n, f in found.candidates.items()}},
    }


def _walk(root: Path):
    for directory, dirs, files in os.walk(root):
        here = Path(directory)
        depth = len(here.relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and depth < MAX_DEPTH
                         and (not d.startswith(".") or d in _HIDDEN_KEPT))
        for name in sorted(files):
            yield here / name


def _classify(found: _Found, path: Path) -> None:
    relative = found.rel(path)
    parts = relative.split("/")
    name, folder = path.name, "/".join(parts[:-1])
    lower = name.lower()
    if relative in _MCP_CONFIGS:
        found.configs.append(path)
        found.read(path)
    elif name == "langgraph.json":
        data = _json(path)
        graphs = data.get("graphs") if isinstance(data, dict) else None
        for graph in sorted(graphs or {}) if isinstance(graphs, dict) else []:
            found.agent(path, "langgraph", graph, basis="langgraph.json")
    elif name in _AGENT_CARDS or relative.endswith(".well-known/agent-card.json"):
        _agent_card(found, path)
    elif folder == ".claude/agents" and lower.endswith(".md"):
        meta = _frontmatter(path)
        found.agent(path, "claude-code", meta.get("name") or path.stem,
                    model=meta.get("model"), basis="Claude Code subagent")
    elif folder == ".github/workflows" and lower.endswith((".yml", ".yaml")):
        _workflow(found, path)
    elif relative == ".github/copilot-instructions.md" or folder in (
            ".github/chatmodes", ".github/agents", ".github/prompts"):
        if not any(a["kind"] == "github-copilot" for a in found.agents):
            found.agent(path, "github-copilot", "GitHub Copilot",
                        basis="Copilot instructions in .github")
    elif _COMPOSE_NAME.match(name):
        _compose(found, path)
    elif lower.endswith(".json") and folder in _EXPORT_DIRS and _export(found, path):
        pass
    elif _CONFIG_NAME.match(name) and not relative.startswith(".github/"):
        _config(found, path)
    elif path.suffix in _CODE_SUFFIXES:
        _code(found, path)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter(path: Path) -> dict[str, Any]:
    """The YAML between a Markdown file's leading `---` lines: name and model only."""
    import yaml

    text = _text(path)
    if not text.startswith("---"):
        return {}
    block = text[3:].split("\n---", 1)[0]
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    if not isinstance(meta, dict):
        return {}
    return {k: str(meta[k]) for k in ("name", "model") if isinstance(meta.get(k), str)}


def _agent_card(found: _Found, path: Path) -> None:
    data = _json(path)
    if not (isinstance(data, dict) and data.get("name") and (
            data.get("skills") is not None or data.get("capabilities") is not None)):
        return
    url = urlparse(str(data.get("url") or ""))
    endpoint = (f"{url.scheme}://{url.netloc.rsplit('@', 1)[-1]}"
                if url.scheme and url.hostname else None)
    found.agent(path, "a2a", data["name"], declaredEndpoint=endpoint, basis="A2A agent card")


def _workflow(found: _Found, path: Path) -> None:
    text = _text(path)
    if "anthropics/claude-code-action" in text:
        found.agent(path, "claude-code-action", path.stem, basis="Claude Code in CI")
    if "copilot" in path.stem.lower():
        found.agent(path, "github-copilot", path.stem, basis="Copilot workflow")


def _compose(found: _Found, path: Path) -> None:
    data = _yaml(path)
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return
    found.read(path)
    for name, service in sorted(services.items()):
        if not isinstance(service, dict):
            continue
        image = service.get("image") if isinstance(service.get("image"), str) else None
        repository = (image or "").rsplit(":", 1)[0] if image and "@" not in image else image
        entry = {"name": str(name), "file": found.rel(path), "image": image,
                 "built": "build" in service}
        role = _ENGINE_IMAGES.get(repository or "") or _AGENT_IMAGES.get(repository or "")
        if role:
            entry["role"] = role
        found.services.append({k: v for k, v in entry.items() if v is not None})
        if repository in _AGENT_IMAGES:
            found.agent(path, _AGENT_IMAGES[repository], str(name), basis="compose service")


def _config(found: _Found, path: Path) -> None:
    data = _yaml(path) if path.suffix.lower() in (".yml", ".yaml") else _json(path)
    if not isinstance(data, dict):
        return
    agents = data.get("agents")
    if isinstance(agents, dict):
        for name, settings in sorted(agents.items()):
            if isinstance(settings, dict):
                model = settings.get("model") if isinstance(settings.get("model"), str) else None
                found.agent(path, "configured", name, model=model,
                            basis="agents in the application's configuration")
    _config_models(found, path, data, provider=None, depth=0)


def _config_models(found: _Found, path: Path, node: Any, provider: str | None,
                   depth: int) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            key = str(key)
            here = key.lower() if key.lower() in _PROVIDERS else provider
            if _MODEL_KEY.match(key) and isinstance(value, str):
                found.model(path, value, here or _family(value))
            else:
                _config_models(found, path, value, here, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _config_models(found, path, item, provider, depth + 1)


def _code(found: _Found, path: Path) -> None:
    text = _text(path)
    if not text:
        return
    imports = (_PY_IMPORT.findall(text) if path.suffix == ".py" else _JS_IMPORT.findall(text))
    seen = set()
    for module in imports:
        for prefix, name in _FRAMEWORKS.items():
            if (module == prefix or module.startswith(prefix)
                    or (path.suffix == ".py" and module.split(".")[0] == prefix)):
                seen.add(name)
                break
    for name in seen:
        entry = found.frameworks.setdefault(name, {"name": name, "files": 0,
                                                   "example": found.rel(path)})
        entry["files"] += 1
    if "StateGraph(" in text and "LangGraph" in seen:
        module = found.rel(path).rsplit(".", 1)[0].replace("/", ".")
        found.agent(path, "langgraph", module, basis="StateGraph built in code")
    for quoted in set(_QUOTED.findall(text)):
        found.candidates.setdefault(quoted, set()).add(found.rel(path))


def _family(name: str) -> str | None:
    """The cloud provider a model name belongs to, by its family, or None."""
    lower = name.lower()
    for prefix, provider in _CLOUD_FAMILIES:
        if lower.startswith(prefix) and (len(prefix) > 2 or lower[len(prefix):][:1] in ("", "-")):
            return provider
    return None


def scope(project: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """The project's scope over what the host showed: which agents run, which models are served.

    An agent the folder declares is `running` at the endpoint of an identified
    agent of the same kind (and, for an A2A card, the same name); a model it
    names is `servedBy` the engine that lists it. A name quoted in code counts
    as a model only when an engine here serves it or it names a cloud family.
    """
    identified = (inventory.get("agentStack") or {}).get("agentEndpoints") or []
    agents = []
    for agent in project["agents"]:
        match = next((a for a in identified if agent["adapter"] != "declared"
                      and a.get("adapter", a.get("kind")) == agent["adapter"]
                      and (agent["kind"] != "a2a" or a.get("name") == agent["name"])), None)
        agents.append({**agent, "running": match.get("endpoint") if match else None})
    served: dict[str, str] = {}
    for engine in inventory.get("engines") or []:
        for model in engine.get("models") or []:
            if model.get("name"):
                served.setdefault(str(model["name"]), str(engine.get("endpoint")))
    models = {m["name"]: dict(m) for m in project["models"]}
    for agent in agents:
        if agent.get("model") and agent["model"] not in models:
            models[agent["model"]] = {"name": agent["model"], "files": [agent["file"]]}
    for name, files in (project.get("paths") or {}).get("codeModels", {}).items():
        family = _family(name)
        if name in models or not (_served(name, served) or (family and "-" in name)):
            continue
        models[name] = {"name": name, "files": files[:5], "basis": "quoted in code",
                        **({"provider": family} if family else {})}
    # `x` and `x:latest` are one model to Ollama: kept under the name the engine serves.
    for name in list(models):
        canonical = f"{name}:latest"
        if ":" not in name and canonical in served and canonical != name:
            entry = models.pop(name)
            merged = models.setdefault(canonical, {"name": canonical, "files": []})
            merged["files"] = sorted({*merged.get("files", []), *entry.get("files", [])})
            for agent in agents:
                if agent.get("model") == name:
                    agent["model"] = canonical
    listed = []
    for name in sorted(models):
        entry = models[name]
        entry["provider"] = entry.get("provider") or _family(name)
        entry["servedBy"] = _served(name, served)
        listed.append({k: v for k, v in entry.items() if v is not None or k == "servedBy"})
    document = {key: value for key, value in project.items() if key != "paths"}
    document.update(agents=agents, models=listed)
    document["hash"] = sha256_json({
        "files": [(f["path"], f["hash"]) for f in project["files"]],
        "agents": [(a["kind"], a["name"]) for a in agents],
        "models": [m["name"] for m in listed],
        "services": [(s["name"], s.get("image")) for s in project.get("services", [])]})
    return document


def _served(name: str, served: dict[str, str]) -> str | None:
    """The engine serving `name`; Ollama answers to `name` as `name:latest` too."""
    return served.get(name) or (served.get(f"{name}:latest") if ":" not in name else None)


def _export(found: _Found, path: Path) -> bool:
    """An n8n workflow or a Flowise chatflow: its agent, if it has one, and its models."""
    try:
        if path.stat().st_size > MAX_EXPORT_BYTES:
            return False
    except OSError:
        return False
    data = _json(path)
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return False
    n8n = [n for n in nodes if isinstance(n, dict) and isinstance(n.get("type"), str)
           and "n8n-nodes" in n["type"]]
    if n8n:
        found.read(path)
        for node in n8n:
            kind = node["type"].rsplit(".", 1)[-1].lower()
            if kind in _N8N_MODEL_NODES:
                found.model(path, (node.get("parameters") or {}).get("model"))
        if any(n["type"].rsplit(".", 1)[-1].lower() == "agent" for n in n8n):
            found.agent(path, "n8n", data.get("name") or path.stem, basis="n8n export")
        return True
    flowise = [n.get("data") for n in nodes if isinstance(n, dict)
               and isinstance(n.get("data"), dict) and isinstance(n["data"].get("name"), str)]
    if not flowise:
        return False
    found.read(path)
    for node in flowise:
        if node["name"].lower() in _FLOWISE_MODEL_NODES:
            found.model(path, (node.get("inputs") or {}).get("modelName"))
    if any("agent" in node["name"].lower() for node in flowise):
        found.agent(path, "flowise", path.stem, basis="Flowise export")
    return True


__all__ = ["MAX_DEPTH", "MAX_FILES", "ProjectError", "scan_project", "scope"]
