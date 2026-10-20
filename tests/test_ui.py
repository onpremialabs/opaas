"""`opaas ui`: the local dashboard server (D79). Driven in process, no socket."""

import json
import threading

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from opaas.adapters import register_backend
from opaas.cli.main import app as cli
from opaas.cli.sprite import MASCOT
from opaas.core.store import list_runs, runs_dir
from opaas.ui.app import COOKIE, create_app
from opaas.ui.jobs import JobError, JobManager, check_command, endpoint_of

from .conftest import FakeBackend

runner = CliRunner()
TOKEN = "test-token"
HOST = "127.0.0.1:8780"


class FakeProcess:
    """A job's process that ends when the test says so."""

    def __init__(self):
        self.done = threading.Event()
        self.terminated = False

    def wait(self):
        self.done.wait(5)
        return 0

    def terminate(self):
        self.terminated = True
        self.done.set()


@pytest.fixture
def launched():
    return []


@pytest.fixture
def client(launched):
    def launcher(argv, cwd, env, log):
        process = FakeProcess()
        launched.append((argv, env, process))
        log.write_text("", encoding="utf-8")
        return process

    store = runs_dir()
    jobs = JobManager(store=store, launcher=launcher)
    app = create_app(store, token=TOKEN, hosts={HOST}, jobs=jobs)
    with TestClient(app, base_url=f"http://{HOST}") as c:
        c.cookies.set(COOKIE, TOKEN)
        yield c


def _post(client, path, body):
    return client.post(path, json=body, headers={"x-opaas-token": TOKEN})


@pytest.fixture
def bench_run():
    register_backend(FakeBackend)
    result = runner.invoke(cli, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
        "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup", "0",
        "--idle-seconds", "0", "--dataset", "opaas-smoke", "--suite", "performance",
        "--suite", "capability"])
    assert result.exit_code == 0, result.output
    (record,) = list_runs()
    return record["runId"]


# --- Local, narrowed (dashboard.md §6) --------------------------------------

def test_a_request_without_the_session_token_is_refused():
    app = create_app(runs_dir(), token=TOKEN, hosts={HOST})
    with TestClient(app, base_url=f"http://{HOST}") as c:
        assert c.get("/").status_code == 403
        assert c.get("/?token=wrong").status_code == 403
        traded = c.get(f"/?token={TOKEN}", follow_redirects=False)
        assert traded.status_code == 303 and traded.headers["location"] == "/"
        cookie = traded.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
        assert c.get("/").status_code == 200


def test_a_foreign_host_is_turned_away(client):
    """Against DNS rebinding: the page must be served under the name it was started on."""
    assert client.get("/", headers={"host": "evil.example:8780"}).status_code == 400


def test_a_post_without_the_session_header_is_refused(client):
    response = client.post("/api/jobs", json={"args": ["discover", "scan"]})
    assert response.status_code == 403


def test_pages_load_nothing_from_elsewhere(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "access-control-allow-origin" not in client.get("/").headers


def test_the_server_refuses_a_non_loopback_address():
    result = runner.invoke(cli, ["ui", "--host", "0.0.0.0"])
    assert result.exit_code == 2 and "loopback" in result.output


# --- Pages are built from the store ----------------------------------------

def test_the_run_page_shows_intervals_files_and_the_command(client, bench_run):
    page = client.get(f"/runs/{bench_run}").text
    assert "opaas run --model fake/model-1b" in page
    assert "<h1>fake/model-1b</h1>" in page  # the model's name, not its record
    assert 'class="ibar"' in page and "No total" in page
    assert "matches run.json" in page and "report.json" in page
    assert "Generate draft v1" in page
    assert bench_run in client.get("/runs").text and bench_run in client.get("/").text


def test_a_runs_file_is_served_as_a_document_with_scripts_off(client, bench_run):
    response = client.get(f"/runs/{bench_run}/files/report.json")
    assert response.status_code == 200
    assert json.loads(response.text)["runId"] == bench_run
    assert response.headers["content-security-policy"].startswith("sandbox")
    # Encoded, so the client cannot normalise it away before the server sees it.
    escape = client.get(f"/runs/{bench_run}/files/%2e%2e/%2e%2e/{bench_run}/run.json")
    assert escape.status_code == 404


def test_events_stream_a_finished_runs_progress_then_its_status(client, bench_run):
    body = client.get(f"/runs/{bench_run}/events").text
    chunks = [c for c in body.split("\n\n") if c]
    lines = [json.loads(c[6:]) for c in chunks if c.startswith("data:")]
    assert lines[0] == {**lines[0], "kind": "start", "message": "opaas run started"}
    assert chunks[-1] == 'event: status\ndata: {"status": "completed"}'


def test_a_missing_run_is_a_page_not_an_error(client):
    response = client.get("/runs/19700101-001")
    assert response.status_code == 200 and "Nothing called" in response.text


def test_the_meerkat_is_the_clis_grid(client):
    """dashboard.md §7: drawn from cli/sprite.py::MASCOT, never redrawn."""
    assert client.get("/mascot.json").json()["rows"] == list(MASCOT)
    head = client.get("/favicon.svg")
    assert head.headers["content-type"].startswith("image/svg+xml") and "<rect" in head.text


# --- Jobs: only measuring commands, one queue per endpoint -----------------

@pytest.mark.parametrize("args", [["sign", "--input", "r.json"], ["keygen"],
                                  ["sentinel", "observe", "-u", "http://x"],
                                  ["sentinel", "approve", "p1", "--by", "me"],
                                  ["certificate", "--report", "r", "--credential", "c"],
                                  ["ui"], []])
def test_the_dashboard_never_launches_a_key_or_the_proxy(args):
    with pytest.raises(JobError):
        check_command(args)


def test_a_rerouted_agent_needs_its_confirmation(client):
    args = ["smith", "run", "--agent", "a2a", "--endpoint", "http://agent.test"]
    refused = _post(client, "/api/jobs", {"args": args})
    assert refused.status_code == 409 and "mock MCP server" in refused.json()["error"]
    assert _post(client, "/api/jobs", {"args": args, "confirmRerouted": True}).status_code == 201


def test_two_jobs_on_one_endpoint_run_one_after_the_other(client, launched):
    args = ["run", "--model", "m", "--backend", "ollama", "--base-url", "http://gpu.test:11434"]
    first = _post(client, "/api/jobs", {"args": args}).json()
    second = _post(client, "/api/jobs", {"args": args}).json()
    elsewhere = _post(client, "/api/jobs", {"args": ["discover", "scan"]}).json()
    assert (first["status"], second["status"], elsewhere["status"]) == (
        "running", "queued", "running")
    argv, env, process = launched[0]
    assert argv[1:4] == ["-m", "opaas", "run"] and env["OPAAS_JOB_ID"] == first["id"]
    process.done.set()
    for _ in range(50):
        if client.get(f"/api/jobs/{second['id']}").json()["status"] == "running":
            break
        threading.Event().wait(0.05)
    assert client.get(f"/api/jobs/{second['id']}").json()["status"] == "running"


def test_a_queued_job_can_be_cancelled_before_it_starts(client):
    args = ["run", "--model", "m", "--backend", "ollama", "--base-url", "http://gpu.test:1"]
    _post(client, "/api/jobs", {"args": args})
    queued = _post(client, "/api/jobs", {"args": args}).json()
    cancelled = _post(client, f"/api/jobs/{queued['id']}/cancel", {}).json()
    assert cancelled["status"] == "cancelled"


def test_a_jobs_endpoint_is_its_engine_or_the_backends_default():
    assert endpoint_of(["run", "--base-url", "http://h:1/v1/"]) == "http://h:1/v1"
    assert endpoint_of(["run", "--model", "m", "--backend", "ollama"]) == "http://localhost:11434"
    assert endpoint_of(["smith", "run", "--endpoint", "http://agent"]) == "http://agent"
    assert endpoint_of(["discover", "scan"]) is None


def test_the_run_a_job_produced_carries_its_id(monkeypatch):
    monkeypatch.setenv("OPAAS_JOB_ID", "abc123")
    register_backend(FakeBackend)
    result = runner.invoke(cli, ["discover", "scan", "--no-containers", "-o",
                                 str(runs_dir().parent / "inv.json")])
    assert result.exit_code == 0, result.output
    (record,) = list_runs()
    assert record["jobId"] == "abc123"


# --- Agents (phase 22) ------------------------------------------------------

@pytest.fixture
def agent_run(tmp_path):
    from .conftest import ScriptedBackend

    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(Answering)
    result = runner.invoke(cli, ["smith", "run", "--agent", "bare-model", "--backend",
                                 "fake-answering", "--model", "m", "-n", "2", "--no-toxicity",
                                 "-s", "pack:banking", "-o", str(tmp_path / "agent.json")])
    assert result.exit_code == 0, result.output
    (record,) = list_runs()
    return record["runId"]


def test_the_agent_page_shows_behaviour_auditability_and_absences(client, agent_run):
    page = client.get(f"/runs/{agent_run}").text
    assert "Behaviour" in page and "Task completed" in page and 'class="ibar"' in page
    # Every dimension is listed, each on its own; a bare model keeps no record.
    # A bare model keeps no record: every metric is not measured, for one reason, said once.
    assert page.count("OPAAS ran the loop") == 1
    assert "ACR, RF, LPC, GB, SPDR, AC, IS" in page
    assert "Not certifiable on its own" in page and "pack:banking" in page
    # An agent is certified within a system audit: no draft of its own.
    assert "Generate draft" not in page and "opaas audit run" in page
    assert "banking-destructive-close-account" in page
    # The job page learns which file is the report from the server: an agent's is named freely.
    from opaas.core.store import load_run
    from opaas.ui import views

    directory = runs_dir() / agent_run
    assert views.report_name(directory, load_run(directory)) == "agent.json"


def test_the_new_audit_page_offers_every_adapter_and_pack(client):
    page = client.get("/new").text
    for adapter in ("http", "mcp", "bare-model", "a2a", "langgraph", "open-webui"):
        assert f"<option>{adapter}</option>" in page or f"<option selected>{adapter}</option>" \
            in page
    assert 'value="pack:banking"' in page and 'value="pack:health-es"' in page
    assert "OPAAS_AGENT_TOKEN" in page and "127.0.0.1:8765/mcp" in page


def test_measured_dimensions_are_drawn_each_on_its_own():
    """An agent that keeps a record: intervals where there are some, the IS level as text."""
    from opaas.ui import views

    report = views._agent({
        "agent": {"adapter": "http", "endpoint": "http://agent.test"},
        "auditability": {
            "ACR": {"measured": True, "point": 0.75, "low": 0.5, "high": 0.9, "n": 8},
            "RF": {"measured": True, "point": 0.9, "n": 8},
            "SPDR": {"measured": False, "reason": "No policy in the scenarios."},
            "AC": {"measured": True, "AC": {"point": 0.5, "low": 0.2, "high": 0.8, "n": 6}},
            "IS": {"levels": ["append-only", "hash-chained"], "weakest": "append-only"},
        },
    })
    metrics = {m["name"]: m for d in report["dimensions"] for m in d["metrics"]}
    assert metrics["ACR"]["row"]["point"] == 0.75 and metrics["RF"]["text"] == "0.9"
    assert metrics["AC"]["row"]["low"] == 0.2 and metrics["IS"]["text"] == "append-only"
    assert metrics["SPDR"]["notMeasured"] == "No policy in the scenarios."
    assert report["auditabilityReason"] is None
    assert report["model"] == "Agent · http · http://agent.test"
