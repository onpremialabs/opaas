"""One environment instead of the whole host: `discover scan --project` (D85, D87)."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from opaas.cli.main import app as cli
from opaas.discover import discover
from opaas.discover.inventory import Listener, scan
from opaas.discover.project import ProjectError, scan_project, scope
from opaas.system.draft import draft_manifest
from opaas.system.manifest import load_manifest

runner = CliRunner()


def _said(result):
    """The output as one line: Rich wraps at the terminal's width, and a long temporary
    path (CI's are) moves where a phrase breaks."""
    return " ".join(result.output.split())


def _engines():
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.24.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen2.5:7b", "digest": "a"}, {"name": "llama3.2:1b", "digest": "b"},
                {"name": "mistral:7b", "digest": "c"}, {"name": "nomic-embed-text:latest",
                                                        "digest": "d"}]})
        return httpx.Response(404)
    return scan({11434: Listener(11434, "127.0.0.1", "ollama")}, environment={},
                transport=httpx.MockTransport(handler))


@pytest.fixture
def project(tmp_path):
    """A project shaped like a real agentic platform: config, Claude Code, CI, code."""
    root = tmp_path / "support-bot"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode" / "mcp.json").write_text(json.dumps({"servers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}}))
    (root / "langgraph.json").write_text(json.dumps({"graphs": {"triage": "./agent.py:graph"}}))
    (root / "workflows").mkdir()
    (root / "workflows" / "tickets.json").write_text(json.dumps({
        "name": "Ticket agent", "nodes": [
            {"type": "@n8n/n8n-nodes-langchain.agent", "parameters": {"text": "secret prompt"}},
            {"type": "@n8n/n8n-nodes-langchain.lmChatOllama",
             "parameters": {"model": "qwen2.5:7b"}, "credentials": {"ollamaApi": {"id": "7"}}}]}))
    (root / "config.yaml").write_text(
        "llm:\n  provider: anthropic\n"
        "ollama:\n  default_model: mistral:7b\n  embed_model: nomic-embed-text\n"
        "anthropic:\n  api_key: 'sk-ant-secret'\n  model: claude-opus-5\n"
        "agents:\n  redactor:\n    model: llama3.2:1b\n  revisor:\n    model: null\n")
    (root / "backend").mkdir()
    (root / "backend" / "config.yaml").write_text("agents:\n  redactor:\n    model: llama3.2:1b\n")
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews with a private rubric\nmodel: sonnet\n"
        "---\nYou are a reviewer.\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "review.yml").write_text(
        "jobs:\n  r:\n    steps:\n      - uses: anthropics/claude-code-action@v1\n")
    (root / ".github" / "copilot-instructions.md").write_text("Prefer small functions.\n")
    (root / "docker-compose.yml").write_text(
        "services:\n  ollama:\n    image: ollama/ollama:latest\n    environment:\n"
        "      - TOKEN=hunter2\n  api:\n    build: .\n")
    (root / "app").mkdir()
    (root / "app" / "graph.py").write_text(
        "from langgraph.graph import StateGraph\nimport anthropic\n"
        "g = StateGraph(dict)\nFALLBACK = 'gpt-4o-mini'\nLOCAL = \"mistral:7b\"\n"
        "OTHER = 'not-a-model'\n")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "dep.py").write_text("from crewai import Agent\n")
    return root


def _scoped(project):
    return discover(engines=_engines(), listeners={}, project=project)


def test_a_project_declares_its_agents_from_config_claude_ci_copilot_and_code(project):
    agents = {(a["kind"], a["name"]) for a in scan_project(project)["agents"]}
    assert agents == {
        ("langgraph", "triage"), ("langgraph", "app.graph"), ("n8n", "Ticket agent"),
        ("configured", "redactor"), ("configured", "revisor"),
        ("claude-code", "code-reviewer"), ("claude-code-action", "review"),
        ("github-copilot", "GitHub Copilot")}


def test_an_agent_declared_in_two_files_is_one_agent(project):
    (redactor,) = [a for a in scan_project(project)["agents"] if a["name"] == "redactor"]
    assert redactor["file"] == "config.yaml" and redactor["alsoIn"] == ["backend/config.yaml"]
    assert redactor["model"] == "llama3.2:1b" and redactor["adapter"] == "declared"


def test_models_come_from_config_with_their_provider_and_from_code_when_grounded(project):
    models = {m["name"]: m for m in _scoped(project)["project"]["models"]}
    # `x` and `x:latest` are one model: the engine's name is kept.
    assert "nomic-embed-text" not in models and "nomic-embed-text:latest" in models
    assert models["claude-opus-5"] == {**models["claude-opus-5"], "provider": "anthropic",
                                       "servedBy": None}
    assert models["mistral:7b"]["servedBy"] == "127.0.0.1:11434"
    # Quoted in code: kept when an engine serves it or it names a cloud family.
    assert models["gpt-4o-mini"]["provider"] == "openai"
    assert "not-a-model" not in models


def test_the_stack_lists_compose_services_and_the_frameworks_code_imports(project):
    found = scan_project(project)
    assert {s["name"]: s.get("image") for s in found["services"]} == {
        "api": None, "ollama": "ollama/ollama:latest"}
    assert {f["name"] for f in found["frameworks"]} == {"LangGraph", "Anthropic SDK"}


def test_installed_packages_are_not_the_projects_declarations(project):
    found = scan_project(project)
    assert not [f for f in found["files"] if ".venv" in f["path"]]
    assert "CrewAI" not in {f["name"] for f in found["frameworks"]}


def test_a_project_scope_is_publishable(project):
    """Names only: never a prompt, a description, a credential or an environment value."""
    text = json.dumps(_scoped(project))
    for secret in ("secret prompt", "ollamaApi", "sk-ant-secret", "hunter2",
                   "private rubric", "You are a reviewer", str(project.parent)):
        assert secret not in text
    assert "paths" not in _scoped(project)["project"]


def test_the_projects_own_mcp_config_is_read_and_the_users_are_not(project):
    document = _scoped(project)
    assert [c["file"] for c in document["agentStack"]["mcpConfigs"]] == ["mcp.json"]
    assert "wellKnownConfigs" not in document["agentStack"]
    assert document["agentStack"]["containers"]["available"] is False


def test_the_scope_is_bound_into_the_environment_hash(project):
    host = discover(engines=_engines(), listeners={})
    assert "project" not in host and _scoped(project)["hash"] != host["hash"]


def test_a_declared_agent_runs_only_where_an_identified_agent_of_its_kind_answers(project):
    inventory = {"engines": [], "agentStack": {"agentEndpoints": [
        {"kind": "langgraph", "adapter": "langgraph", "name": "LangGraph API",
         "endpoint": "127.0.0.1:2024"}]}}
    running = {a["name"]: a["running"] for a in scope(scan_project(project), inventory)["agents"]}
    assert running["triage"] == "127.0.0.1:2024"
    # An agent nothing can drive is never matched to an endpoint by kind.
    assert running["redactor"] is None and running["code-reviewer"] is None


def test_a_scoped_draft_is_the_whole_environment_and_a_valid_manifest(project):
    manifest, notes = draft_manifest(_scoped(project))
    load_manifest(manifest, known_adapters=("langgraph", "n8n"))
    by_kind: dict = {}
    for c in manifest["components"]:
        by_kind.setdefault(c["kind"], []).append(c)
    # Local models to benchmark; the embedding model is not one of them.
    assert sorted(c["model"] for c in by_kind["model"]) == [
        "llama3.2:1b", "mistral:7b", "qwen2.5:7b"]
    assert any("nomic-embed-text:latest is an embedding model" in n for n in notes)
    # Cloud models are listed as external: never certified (D22).
    assert {c["name"] for c in by_kind["external"]} == {"claude-opus-5", "gpt-4o-mini"}
    # Every declared agent is in the manifest, pending until something drives it.
    assert {c["adapter"] for c in by_kind["agent"]} == {"declared"}
    assert len(by_kind["agent"]) == 8
    redactor = next(c for c in by_kind["agent"] if c["name"] == "redactor")
    llama = next(c for c in by_kind["model"] if c["model"] == "llama3.2:1b")
    assert {"from": redactor["id"], "to": llama["id"]} in manifest["flows"]


def test_a_project_that_is_not_a_folder_is_refused(tmp_path):
    with pytest.raises(ProjectError, match="not a folder"):
        scan_project(tmp_path / "missing")
    result = runner.invoke(cli, ["discover", "scan", "--project", str(tmp_path / "missing"),
                                 "-o", str(tmp_path / "inventory.json")])
    assert result.exit_code == 2 and "not a folder" in _said(result)


def test_an_empty_draft_says_nothing_was_found_instead_of_an_invalid_manifest(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    result = runner.invoke(cli, ["audit", "draft", "--project", str(root),
                                 "-o", str(root / "system.json")])
    assert result.exit_code == 2 and "Nothing was found" in _said(result)
    assert not (root / "system.json").exists()


def test_discover_scan_takes_a_project_and_prints_its_scope(project, tmp_path):
    out = tmp_path / "inventory.json"
    result = runner.invoke(cli, ["discover", "scan", "--project", str(project), "-o", str(out)])
    assert result.exit_code == 0, result.output
    said = _said(result)
    assert "Project support-bot" in said and "not running" in said
    assert "service ollama" in said and "frameworks" in said
    assert json.loads(out.read_text())["inventory"]["project"]["root"] == "support-bot"
