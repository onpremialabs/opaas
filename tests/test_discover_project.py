"""One environment instead of the whole host: `discover scan --project` (D85)."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from opaas.cli.main import app as cli
from opaas.discover import discover
from opaas.discover.inventory import Listener, scan
from opaas.discover.project import ProjectError, scan_project, scope
from opaas.system.draft import draft_manifest

runner = CliRunner()


def _engines():
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.24.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen2.5:7b", "digest": "a"}, {"name": "llama3.2:1b", "digest": "b"},
                {"name": "mistral:7b", "digest": "c"}]})
        return httpx.Response(404)
    return scan({11434: Listener(11434, "127.0.0.1", "ollama")}, environment={},
                transport=httpx.MockTransport(handler))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "support-bot"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode" / "mcp.json").write_text(json.dumps({"servers": {
        "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}}))
    (root / "langgraph.json").write_text(json.dumps({"graphs": {
        "triage": "./agent.py:graph", "answer": "./answer.py:graph"}}))
    (root / "workflows").mkdir()
    (root / "workflows" / "tickets.json").write_text(json.dumps({
        "name": "Ticket agent", "nodes": [
            {"type": "@n8n/n8n-nodes-langchain.agent", "parameters": {"text": "secret prompt"}},
            {"type": "@n8n/n8n-nodes-langchain.lmChatOllama",
             "parameters": {"model": "qwen2.5:7b"}, "credentials": {"ollamaApi": {"id": "7"}}}]}))
    (root / "workflows" / "notes.json").write_text(json.dumps({"title": "not an export"}))
    (root / "docker-compose.yml").write_text("services: {}\n")
    (root / "agent.py").write_text("MODEL = 'mistral:7b'\n")
    return root


def test_a_project_declares_its_agents_models_and_configs_from_fixed_places(project):
    found = scan_project(project)
    assert [(a["kind"], a["name"]) for a in found["agents"]] == [
        ("langgraph", "answer"), ("langgraph", "triage"), ("n8n", "Ticket agent")]
    # Named in an export's model node: seen. Named only in code: not.
    assert found["models"] == ["qwen2.5:7b"]
    assert [f["path"] for f in found["files"]] == [
        ".vscode/mcp.json", "langgraph.json", "workflows/tickets.json"]
    assert {n["path"] for n in found["notRead"]} == {"docker-compose.yml", "source code"}
    assert found["paths"]["configs"] == [project / ".vscode" / "mcp.json"]


def test_a_project_scope_is_publishable(project):
    """The folder by its name, files by relative path; never a prompt or a credential."""
    document = discover(engines=_engines(), listeners={}, project=project)
    text = json.dumps(document)
    assert str(project.parent) not in text and "secret prompt" not in text
    assert "ollamaApi" not in text and "paths" not in document["project"]
    assert document["project"]["root"] == "support-bot"


def test_a_scoped_scan_says_which_models_are_served_and_which_agents_run(project):
    document = discover(engines=_engines(), listeners={}, project=project)
    scoped = document["project"]
    assert scoped["models"] == [{"name": "qwen2.5:7b", "servedBy": "127.0.0.1:11434"}]
    assert all(a["running"] is None for a in scoped["agents"])
    # The project's own MCP config is read; the user's and the host's containers are not.
    assert [c["file"] for c in document["agentStack"]["mcpConfigs"]] == ["mcp.json"]
    assert "wellKnownConfigs" not in document["agentStack"]
    assert document["agentStack"]["containers"]["available"] is False


def test_the_scope_is_bound_into_the_environment_hash(project):
    host = discover(engines=_engines(), listeners={})
    scoped = discover(engines=_engines(), listeners={}, project=project)
    assert "project" not in host
    assert scoped["hash"] != host["hash"]


def test_a_declared_agent_runs_only_where_an_identified_agent_of_its_kind_answers(project):
    found = scan_project(project)
    inventory = {"engines": [], "agentStack": {"agentEndpoints": [
        {"kind": "langgraph", "adapter": "langgraph", "name": "LangGraph API",
         "endpoint": "127.0.0.1:2024"}]}}
    agents = {a["name"]: a["running"] for a in scope(found, inventory)["agents"]}
    assert agents == {"answer": "127.0.0.1:2024", "triage": "127.0.0.1:2024",
                      "Ticket agent": None}


def test_a_scoped_draft_takes_only_the_projects_models_and_says_what_it_left(project):
    document = discover(engines=_engines(), listeners={}, project=project)
    manifest, notes = draft_manifest(document)
    models = [c["model"] for c in manifest["components"] if c["kind"] == "model"]
    assert models == ["qwen2.5:7b"]
    assert not [c for c in manifest["components"] if c["kind"] == "agent"]
    assert any("'triage' is declared in langgraph.json and is not running" in n for n in notes)
    assert any("2 model(s) and agent(s) this host serves are not the project's" in n
               for n in notes)


def test_a_project_that_names_no_model_is_told_so_not_given_every_model(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "langgraph.json").write_text(json.dumps({"graphs": {"agent": "./a.py:g"}}))
    manifest, notes = draft_manifest(discover(engines=_engines(), listeners={}, project=root))
    assert not [c for c in manifest["components"] if c["kind"] == "model"]
    assert any("names no model" in n for n in notes)


def test_a_project_that_is_not_a_folder_is_refused(tmp_path):
    with pytest.raises(ProjectError, match="not a folder"):
        scan_project(tmp_path / "missing")
    result = runner.invoke(cli, ["discover", "scan", "--project", str(tmp_path / "missing"),
                                 "-o", str(tmp_path / "inventory.json")])
    assert result.exit_code == 2 and "not a folder" in result.output


def test_discover_scan_takes_a_project_and_prints_its_scope(project, tmp_path):
    out = tmp_path / "inventory.json"
    result = runner.invoke(cli, ["discover", "scan", "--project", str(project), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Project support-bot" in result.output and "not running" in result.output
    assert json.loads(out.read_text())["inventory"]["project"]["root"] == "support-bot"
