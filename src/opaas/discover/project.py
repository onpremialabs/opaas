"""One environment, not the whole host: what a project folder declares (D85).

A host scan finds every engine and every model it serves, but an agent only
when it is running and identifies itself on a port (D63). An operator who
wants one deployment audited, out of several on the same machine, names its
folder instead: `discover scan --project DIR`.

**Fixed places, never a crawl.** The folder is the operator's; inside it,
OPAAS reads only the files that conventionally declare an agent stack, at
their conventional paths, a level or two deep (`_MCP_CONFIGS`, `_SKILL_DIRS`,
`_AGENT_FILES`, the JSON in `_EXPORT_DIRS`). What it does not read is said in
`notRead`: YAML (a compose file, a Dify DSL) because OPAAS carries no YAML
parser, and code, because what a program would do is not read off its text.

**What belongs to the environment** is what those files name:

- its MCP configurations and skill directories, read as `--config` and
  `--skills` would read them;
- its agents: the graphs of a `langgraph.json`, an A2A agent card, the
  workflows of an n8n or Flowise export that contain an agent node;
- its models: those the exports name in their model nodes. A model named in
  code is not seen, and the draft says the environment named none rather than
  taking every model the engine serves.

The host is still observed, and the inventory keeps what it saw: the scope
says which of it belongs to the environment (`project.agents[].running`,
`project.models[].servedBy`). An agent the folder declares that is not
running is listed as such, never given an endpoint by guess.

**Publishable (invariant 12).** The folder is recorded by its name, files by
their path relative to it with their hash. From an export, only node types,
model names and workflow names are kept: never prompts, credentials or URLs
beyond scheme, host and port.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opaas.core.hashing import sha256_file, sha256_json

#: MCP configurations a project keeps, by client.
_MCP_CONFIGS = (".mcp.json", "mcp.json", ".vscode/mcp.json", ".cursor/mcp.json",
                ".roo/mcp.json", ".gemini/settings.json")
#: Skill directories a project keeps.
_SKILL_DIRS = (".claude/skills", "skills")
#: Files that declare an agent by their name.
_AGENT_FILES = ("langgraph.json", ".well-known/agent-card.json", ".well-known/agent.json",
                "agent-card.json")
#: Where exported workflows (n8n, Flowise) are kept: the folder itself and these.
_EXPORT_DIRS = (".", "workflows", "chatflows", "flows", "n8n", "flowise", "agents")
#: Files read and named as not read: their format, or what they are.
_NOT_READ = {"docker-compose.yml": "YAML", "docker-compose.yaml": "YAML",
             "compose.yml": "YAML", "compose.yaml": "YAML", "langgraph.yaml": "YAML"}
#: A JSON export larger than this is not an agent declaration: not read.
MAX_EXPORT_BYTES = 2_000_000

#: Model nodes, and the parameter that names the model (n8n: parameters; Flowise: inputs).
_N8N_MODEL_NODES = ("lmchatollama", "lmollama", "lmchatopenai", "lmopenai")
_FLOWISE_MODEL_NODES = ("chatollama", "ollama", "chatopenai", "chatlocalai", "chatopenaicustom")


class ProjectError(ValueError):
    """A project folder that cannot be scanned, with what to do instead."""


def scan_project(root: Path) -> dict[str, Any]:
    """What `root` declares: MCP configs and skill dirs to read, agents and models it names.

    Returns the publishable scope (`root`, `files`, `agents`, `models`,
    `notRead`) and, under `paths`, the local paths `discover` reads next. The
    caller drops `paths` before anything is written.
    """
    root = root.expanduser()
    if not root.is_dir():
        raise ProjectError(f"{root} is not a folder: name the folder of the environment to audit.")
    root = root.resolve()
    files: list[dict[str, str]] = []
    configs: list[Path] = []
    skill_dirs: list[Path] = []
    agents: list[dict[str, Any]] = []
    models: set[str] = set()

    def note(path: Path) -> None:
        files.append({"path": path.relative_to(root).as_posix(),
                      "hash": "sha256:" + sha256_file(path)})

    for relative in _MCP_CONFIGS:
        path = root / relative
        if path.is_file():
            configs.append(path)
            note(path)
    for relative in _SKILL_DIRS:
        path = root / relative
        if path.is_dir():
            skill_dirs.append(path)
    for relative in _AGENT_FILES:
        path = root / relative
        if path.is_file():
            found = _agent_file(path)
            if found:
                note(path)
                agents.extend({**a, "file": path.relative_to(root).as_posix()} for a in found)
    seen: set[Path] = set()
    for folder in _EXPORT_DIRS:
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path in seen or path.name in _AGENT_FILES or path.name in (
                    p.split("/")[-1] for p in _MCP_CONFIGS):
                continue
            seen.add(path)
            export = _export(path)
            if export is None:
                continue
            note(path)
            models.update(export["models"])
            agents.extend({**a, "file": path.relative_to(root).as_posix()}
                          for a in export["agents"])
    not_read = [{"path": name, "reason": f"{why}: OPAAS reads no {why} file."}
                for name, why in sorted(_NOT_READ.items()) if (root / name).is_file()]
    not_read.append({"path": "source code", "reason": "What a program calls is not read "
                     "off its text: a model or agent named only in code is not seen."})
    agents.sort(key=lambda a: (a["kind"], a["name"], a["file"]))
    return {
        "root": root.name,
        "files": sorted(files, key=lambda f: f["path"]),
        "agents": agents,
        "models": sorted(models),
        "notRead": not_read,
        "paths": {"configs": configs, "skillDirs": skill_dirs},
    }


def scope(project: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """The project's scope over what the host showed: which agents run, which models are served.

    An agent the folder declares is `running` at the endpoint of an identified
    agent of the same kind (and, for an A2A card, the same name); a model it
    names is `servedBy` the engine that lists it. Neither is guessed further.
    """
    identified = (inventory.get("agentStack") or {}).get("agentEndpoints") or []
    agents = []
    for agent in project["agents"]:
        match = next((a for a in identified if a.get("adapter", a.get("kind")) == agent["adapter"]
                      and (agent["kind"] != "a2a" or a.get("name") == agent["name"])), None)
        agents.append({**agent, "running": match.get("endpoint") if match else None})
    served: dict[str, str] = {}
    for engine in inventory.get("engines") or []:
        for model in engine.get("models") or []:
            if model.get("name"):
                served.setdefault(str(model["name"]), str(engine.get("endpoint")))
    models = [{"name": name, "servedBy": _served(name, served)} for name in project["models"]]
    document = {key: value for key, value in project.items() if key != "paths"}
    document.update(agents=agents, models=models)
    document["hash"] = sha256_json({"files": [(f["path"], f["hash"]) for f in project["files"]],
                                    "agents": [(a["kind"], a["name"]) for a in agents],
                                    "models": project["models"]})
    return document


def _served(name: str, served: dict[str, str]) -> str | None:
    """The engine serving `name`; Ollama answers to `name` as `name:latest` too."""
    return served.get(name) or (served.get(f"{name}:latest") if ":" not in name else None)


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_EXPORT_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _agent_file(path: Path) -> list[dict[str, Any]]:
    """The agents a `langgraph.json` or an A2A card declares."""
    data = _read_json(path)
    if not isinstance(data, dict):
        return []
    if path.name == "langgraph.json":
        graphs = data.get("graphs")
        if not isinstance(graphs, dict):
            return []
        return [{"kind": "langgraph", "adapter": "langgraph", "name": str(name)}
                for name in sorted(graphs)]
    if data.get("name") and (data.get("skills") is not None
                             or data.get("capabilities") is not None):
        agent = {"kind": "a2a", "adapter": "a2a", "name": str(data["name"])}
        url = urlparse(str(data.get("url") or ""))
        if url.scheme and url.hostname:
            agent["declaredEndpoint"] = f"{url.scheme}://{url.netloc.rsplit('@', 1)[-1]}"
        return [agent]
    return []


def _export(path: Path) -> dict[str, Any] | None:
    """An n8n workflow or a Flowise chatflow: its agent, if it has one, and its models."""
    data = _read_json(path)
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return None
    n8n = [n for n in nodes if isinstance(n, dict) and isinstance(n.get("type"), str)
           and ("n8n-nodes" in n["type"])]
    if n8n:
        models = set()
        for node in n8n:
            kind = node["type"].rsplit(".", 1)[-1].lower()
            model = (node.get("parameters") or {}).get("model")
            if kind in _N8N_MODEL_NODES and isinstance(model, str) and model:
                models.add(model)
        has_agent = any(n["type"].rsplit(".", 1)[-1].lower() == "agent" for n in n8n)
        name = str(data.get("name") or path.stem)
        return {"agents": [{"kind": "n8n", "adapter": "n8n", "name": name}] if has_agent else [],
                "models": models}
    flowise = [n.get("data") for n in nodes if isinstance(n, dict)
               and isinstance(n.get("data"), dict) and isinstance(n["data"].get("name"), str)]
    if not flowise:
        return None
    models = set()
    for node in flowise:
        model = (node.get("inputs") or {}).get("modelName")
        if node["name"].lower() in _FLOWISE_MODEL_NODES and isinstance(model, str) and model:
            models.add(model)
    has_agent = any("agent" in node["name"].lower() for node in flowise)
    return {"agents": [{"kind": "flowise", "adapter": "flowise", "name": path.stem}]
            if has_agent else [], "models": models}


__all__ = ["MAX_EXPORT_BYTES", "ProjectError", "scan_project", "scope"]
