"""`opaas ui`: the local dashboard server (D79). Driven in process, no socket."""

import json
import threading
from pathlib import Path

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
    assert "Generate certificate v1" in page
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
    # An agent is certified within a system audit: no certificate of its own.
    assert "Generate certificate" not in page and "opaas audit run" in page
    assert "banking-destructive-close-account" in page
    # The job page learns which file is the report from the server: an agent's is named freely.
    from opaas.core.store import load_run
    from opaas.ui import views

    directory = runs_dir() / agent_run
    assert views.report_name(directory, load_run(directory)) == "agent.json"


def test_the_new_audit_page_offers_the_scan_and_the_system_audit_only(client):
    """The machine is scanned first, then audited as one system: no per-model or
    per-agent picker, no free-text forms beside it."""
    page = client.get("/new").text
    assert "data-scan" in page and "data-lookout" in page and 'id="launch-system"' in page
    for gone in ("data-pick", 'id="launch-bench"', 'id="launch-agent"', 'id="launch-discover"'):
        assert gone not in page
    # Each job runs in its own directory: the default manifest is named absolutely.
    default = page.split('name="manifest" value="')[1].split('"')[0]
    assert Path(default).is_absolute() and default.endswith("system.json")


def test_the_overview_says_what_opaas_measures_from_its_registries(client):
    from opaas.bench.suites import BLOCKS
    from opaas.core.metrics import DIMENSIONS
    from opaas.system.orchestrate import STEPS

    page = client.get("/").text
    assert "What OPAAS does" in page
    # What the machine has cached is the terminal menu's business, not the overview's.
    assert "This machine" not in page and "unmeasured" not in page
    for name in BLOCKS.values():
        assert name in page.replace("&amp;", "&")
    for entry in DIMENSIONS.values():
        assert all(f'<span class="mono">{m}</span>' in page for m in entry["metrics"])
    for step in STEPS:
        assert f"<b>{step}</b>" in page


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


# --- Systems (phase 22) ----------------------------------------------------

@pytest.fixture
def system_run(tmp_path):
    from .conftest import ScriptedBackend

    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(FakeBackend)
    register_backend(Answering)
    system = tmp_path / "system.json"
    system.write_text(json.dumps({
        "format": 1, "id": "support", "purpose": "Answers employees from documents.",
        "components": [
            {"id": "llm", "kind": "model", "backend": "fake", "model": "fake/model-1b"},
            {"id": "agent", "kind": "agent", "adapter": "bare-model",
             "backend": "fake-answering", "model": "m"}],
        "flows": [{"from": "agent", "to": "llm"}]}), encoding="utf-8")
    result = runner.invoke(cli, ["audit", "run", str(system), "-o", str(tmp_path / "out"),
                                 "--skip", "discover", "--bench-args",
                                 "--suite capability --dataset opaas-smoke --max-tokens 8",
                                 "-n", "1", "--no-open"])
    assert result.exit_code == 0, result.output
    (record,) = [r for r in list_runs() if r["kind"] == "audit"]
    return record["runId"]


def test_the_system_page_shows_coverage_card_composition_and_members(client, system_run):
    page = client.get(f"/runs/{system_run}").text
    assert "<h1>System · support</h1>" in page and "Answers employees" in page
    assert "Not certifiable as it stands" in page
    # A state per cell, never a grade: three shapes, and the reason on hover.
    assert "cell-measured" in page and "cell-not-measured" in page
    assert "cell-not-applicable" in page and 'title="not-measured: ' in page
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"):
        assert f"<h3>{q} · " in page
    assert "Action recoverability" in page and "actionRecoverability</h3>" not in page
    assert "agent → llm" in page and "system-audit/bench-llm.json" in page
    assert "Generate certificate v1" in page  # a system report can be certified


def test_validating_a_manifest_is_a_job_drafting_one_too():
    check_command(["audit", "validate", "system.json"])
    check_command(["audit", "draft", "-o", "system.json"])


# --- Discover-first `/new`, batch launches, live audit steps ----------------

@pytest.fixture
def discover_run(tmp_path):
    result = runner.invoke(cli, ["discover", "scan", "--no-containers",
                                 "-o", str(tmp_path / "inventory.json")])
    assert result.exit_code == 0, result.output
    (record,) = [r for r in list_runs() if r["kind"] == "discover"]
    return record["runId"]


def test_discover_latest_finds_a_fresh_scan(client, discover_run):
    body = client.get("/api/discover/latest").json()
    assert body["runId"] == discover_run and body["ageSeconds"] >= 0


def test_discover_latest_is_none_without_a_recent_scan(client):
    assert client.get("/api/discover/latest").json() == {"runId": None}


def test_discover_picker_shapes_engines_and_agents(client, discover_run):
    body = client.get(f"/api/discover/{discover_run}").json()
    assert body["runId"] == discover_run
    assert isinstance(body["engines"], list) and isinstance(body["agents"], list)
    # The system audit drafts its manifest from this very scan.
    assert Path(body["inventoryPath"]).is_file()


def test_discover_picker_is_missing_for_an_unknown_run(client):
    response = client.get("/api/discover/20260101-999")
    assert response.status_code == 404


def test_batch_page_lists_every_job_id(client):
    page = client.get("/batch?ids=aaa111,bbb222").text
    assert 'data-job-row="aaa111"' in page and 'data-job-row="bbb222"' in page


@pytest.fixture
def audit_job_run(tmp_path, monkeypatch):
    """A system audit launched the way the dashboard launches jobs: every nested
    step it runs in-process (`cli/audit.py`'s `invoke()`) inherits the same
    `OPAAS_JOB_ID`, exactly as it does when `opaas ui` sets that variable."""
    from .conftest import ScriptedBackend

    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(FakeBackend)
    register_backend(Answering)
    system = tmp_path / "system.json"
    system.write_text(json.dumps({
        "format": 1, "id": "support", "purpose": "Answers employees from documents.",
        "components": [
            {"id": "llm", "kind": "model", "backend": "fake", "model": "fake/model-1b"},
            {"id": "agent", "kind": "agent", "adapter": "bare-model",
             "backend": "fake-answering", "model": "m"}],
        "flows": [{"from": "agent", "to": "llm"}]}), encoding="utf-8")
    monkeypatch.setenv("OPAAS_JOB_ID", "job-xyz")
    result = runner.invoke(cli, ["audit", "run", str(system), "-o", str(tmp_path / "out"),
                                 "--skip", "discover", "--bench-args",
                                 "--suite capability --dataset opaas-smoke --max-tokens 8",
                                 "-n", "1", "--no-open"])
    assert result.exit_code == 0, result.output
    (record,) = [r for r in list_runs() if r["kind"] == "audit"]
    return record["runId"], "job-xyz"


def test_run_for_job_finds_the_launched_run_not_a_nested_step(audit_job_run):
    """Every step `opaas audit run` takes shares its job id (D60's own-process
    steps); the launched run is the one that started first, never a step."""
    from opaas.ui import views

    audit_run, job_id = audit_job_run
    found = views.run_for_job(runs_dir(), job_id)
    assert found is not None
    assert found["runId"] == audit_run and found["kind"] == "audit"


def test_audit_live_steps_pairs_progress_lines_with_their_nested_runs(audit_job_run):
    from opaas.ui import views

    audit_run, job_id = audit_job_run
    store = runs_dir()
    record = views.run_for_job(store, job_id)
    steps = views.audit_live_steps(store, record)
    assert [s["name"] for s in steps] == [
        "discover", "bench", "smith", "sentinel", "inquest", "swarm"]
    discover = next(s for s in steps if s["name"] == "discover")
    assert discover["invocations"] == []  # --skip discover: no line, no run
    bench = next(s for s in steps if s["name"] == "bench")
    assert len(bench["invocations"]) == 1
    assert bench["invocations"][0]["status"] in ("completed", "partial")


def test_audit_live_steps_is_none_off_an_audit(bench_run):
    from opaas.ui import views

    record = views.load_run(runs_dir() / bench_run)
    assert views.audit_live_steps(runs_dir(), record) is None


def test_the_dashboard_has_no_sentinel_page(client):
    """The proxy sits on a live system's data path and is run from a terminal (D46);
    the dashboard neither starts it nor follows it."""
    assert "/sentinel" not in client.get("/").text
    assert client.get("/sentinel").status_code == 404


# --- History and comparison (phase 23) --------------------------------------

def _bench_with(quantization):
    register_backend(FakeBackend)
    result = runner.invoke(cli, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--suite", "capability",
        "--dataset", "opaas-smoke", "--quantization", quantization])
    assert result.exit_code == 0, result.output
    return max(r["runId"] for r in list_runs() if r["kind"] == "bench")


def test_the_history_filters_by_kind_model_status_and_date(client, bench_run, tmp_path):
    from opaas.ui import views

    runner.invoke(cli, ["discover", "scan", "--no-containers", "-o", str(tmp_path / "i.json")])
    every = views.runs(runs_dir())
    assert {r["kind"] for r in views.filter_runs(every, {"kind": "bench"})} == {"bench"}
    assert [r["runId"] for r in views.filter_runs(every, {"model": "MODEL-1B"})] == [bench_run]
    assert views.filter_runs(every, {"since": "2999-01-01"}) == []
    assert len(views.filter_runs(every, {"status": "completed"})) == 2
    bench = next(r for r in every if r["runId"] == bench_run)
    # The engine as the report names it, version included; the filter matches a part of it.
    assert bench["backend"] == "Fake Engine 0.0" and bench["fingerprint"].startswith("sha256:")
    assert views.filter_runs(every, {"backend": "fake engine"}) == [bench]
    assert views.filter_runs(every, {"fingerprint": bench["fingerprint"][7:15]}) == [bench]
    page = client.get("/runs", params={"kind": "bench"}).text
    assert "1 of 2 runs" in page and "data-compare-pick" in page


def test_the_comparison_page_says_what_opaas_compare_says(client, tmp_path):
    """Phase 23's done-when: the dashboard's comparison is `opaas compare`'s, word for word."""
    from html import escape

    first, second = _bench_with("Q8_0"), _bench_with("Q4_K_M")
    reports = [str(runs_dir() / run / "report.json") for run in (first, second)]
    out = tmp_path / "comparison.json"
    result = runner.invoke(cli, ["compare", "--report", reports[0], "--report", reports[1],
                                 "--out", str(out)])
    assert result.exit_code == 0, result.output
    comparison = json.loads(out.read_text(encoding="utf-8"))
    (record,) = [r for r in list_runs() if r["kind"] == "compare"]
    page = client.get(f"/runs/{record['runId']}").text
    assert "Comparison · 2 runs" in page and "in the order given, not ranked" in page
    assert comparison["capability"], "the fake runs measured block B"
    for row in comparison["capability"]:
        assert f"<h3>{escape(row['category'])}</h3>" in page
        assert escape(row["verdict"]["detail"]) in page
    for run in (first, second):
        assert f'href="/runs/{run}"' in page
    assert "Generate certificate" not in page and "certifies nothing" in page


def test_a_jobs_output_is_plain_lines_whatever_the_server_forces(client, launched, monkeypatch):
    """A job writes to a file: no progress-bar redraws, even with FORCE_COLOR set."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    _post(client, "/api/jobs", {"args": ["discover", "scan"]})
    _, env, _ = launched[-1]
    assert env["TTY_COMPATIBLE"] == "0" and "FORCE_COLOR" not in env


def test_two_runs_of_one_configuration_are_told_apart_by_their_run_id():
    from opaas.ui import views

    same = "m · Q4_K_M (abc)"
    page = views._comparison({
        "opaasBenchComparison": "1.0",
        "runs": [{"label": same, "runId": "r1", "source": "/s/20261020-005/report.json"},
                 {"label": same, "runId": "r2", "source": "/s/20261019-002/report.json"}],
        "capability": [{"category": "reasoning", "verdict": {"comparable": True},
                        "runs": [{"label": same, "accuracy": {"point": 1, "low": .6, "high": 1}},
                                 {"label": same, "accuracy": None}]}],
    })
    labels = [bar["label"] for bar in page["capability"][0]["bars"]]
    assert labels == [f"20261020-005 · {same}", f"20261019-002 · {same}"]
    assert page["capability"][0]["bars"][1]["missing"] is True


# --- Comparing agents and systems (D80) --------------------------------------

def _answering_backend():
    from .conftest import ScriptedBackend

    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(FakeBackend)
    register_backend(Answering)


def _smith_report(tmp_path, name):
    result = runner.invoke(cli, ["smith", "run", "--agent", "bare-model", "--backend",
                                 "fake-answering", "--model", "m", "-n", "2", "--no-toxicity",
                                 "-s", "pack:banking", "-o", str(tmp_path / f"{name}.json")])
    assert result.exit_code == 0, result.output
    record = max((r for r in list_runs() if r["kind"] == "smith"), key=lambda r: r["runId"])
    return record["runId"], str(runs_dir() / record["runId"] / f"{name}.json")


def _audit_report(tmp_path, name):
    system = tmp_path / f"{name}.json"
    system.write_text(json.dumps({
        "format": 1, "id": "support", "purpose": "Answers employees from documents.",
        "components": [
            {"id": "llm", "kind": "model", "backend": "fake", "model": "fake/model-1b"},
            {"id": "agent", "kind": "agent", "adapter": "bare-model",
             "backend": "fake-answering", "model": "m"}],
        "flows": [{"from": "agent", "to": "llm"}]}), encoding="utf-8")
    result = runner.invoke(cli, ["audit", "run", str(system), "-o", str(tmp_path / name),
                                 "--skip", "discover", "--bench-args",
                                 "--suite capability --dataset opaas-smoke --max-tokens 8",
                                 "-n", "1", "--no-open"])
    assert result.exit_code == 0, result.output
    record = max((r for r in list_runs() if r["kind"] == "audit"), key=lambda r: r["runId"])
    return record["runId"], str(runs_dir() / record["runId"] / "system-audit"
                                / "system-report.json")


def _compared(tmp_path, paths):
    out = tmp_path / "comparison.json"
    args = ["compare"]
    for path in paths:
        args += ["--report", path]
    result = runner.invoke(cli, [*args, "--out", str(out)])
    assert result.exit_code == 0, result.output
    (record,) = [r for r in list_runs() if r["kind"] == "compare"]
    return json.loads(out.read_text(encoding="utf-8")), record["runId"]


def test_the_agent_comparison_page_says_what_opaas_compare_says(client, tmp_path):
    from html import escape

    _answering_backend()
    runs = [_smith_report(tmp_path, name) for name in ("first", "second")]
    comparison, compare_run = _compared(tmp_path, [path for _, path in runs])
    page = client.get(f"/runs/{compare_run}").text
    assert "Agent comparison · 2 runs" in page and "in the order given, not ranked" in page
    figures = comparison["behaviour"] + [m for d in comparison["auditability"]
                                         for m in d["metrics"]]
    for figure in figures:
        assert escape(figure["verdict"]["detail"]) in page
    for question in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        assert f'<h3 class="dim-title">{question} · ' in page
    for run, _ in runs:
        assert f'href="/runs/{run}"' in page
    assert "Generate certificate" not in page and "certifies nothing" in page
    # The history offers both agent runs, tagged with their kind.
    history = client.get("/runs", params={"kind": "smith"}).text
    assert history.count('data-compare-kind="agent"') == 2


def test_the_system_comparison_page_says_what_opaas_compare_says(client, tmp_path):
    from html import escape

    _answering_backend()
    runs = [_audit_report(tmp_path, name) for name in ("one", "two")]
    comparison, compare_run = _compared(tmp_path, [path for _, path in runs])
    page = client.get(f"/runs/{compare_run}").text
    assert "System comparison · 2 runs" in page and "Coverage, cell by cell" in page
    # A cell per row and dimension, its status the CLI's.
    for cell in comparison["cells"]:
        assert f'aria-label="{cell["status"]}"' in page
    measured = [m for c in comparison["cells"] for m in c.get("measurements", [])]
    assert measured, "both audits measured block B of the model"
    for figure in measured:
        assert escape(figure["verdict"]["detail"]) in page
    for run, _ in runs:
        assert f'href="/runs/{run}"' in page
    history = client.get("/runs", params={"kind": "audit"}).text
    assert history.count('data-compare-kind="system"') == 2


# --- One environment or the whole machine (D85) -----------------------------

def test_new_audit_offers_an_environment_or_the_whole_machine(client):
    page = client.get("/new").text
    assert 'value="project" checked' in page and 'value="host"' in page
    assert "data-browse" in page and "data-scan-preview" in page
    # The environment starts as the folder `opaas ui` was started from.
    assert f'name="project" value="{Path.cwd()}"' in page


def test_the_folder_picker_lists_folders_only(client, tmp_path):
    (tmp_path / "support-bot").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "secrets.env").write_text("TOKEN=x")
    listing = client.get("/api/folders", params={"path": str(tmp_path)}).json()
    assert listing["folders"] == ["support-bot"] and listing["parent"]
    assert client.get("/api/folders", params={"path": str(tmp_path / "secrets.env")}
                      ).status_code == 404


def test_a_scoped_scan_reaches_the_page_with_its_scope(client, tmp_path):
    root = tmp_path / "bot"
    root.mkdir()
    (root / "langgraph.json").write_text(json.dumps({"graphs": {"agent": "./a.py:g"}}))
    result = runner.invoke(cli, ["discover", "scan", "--project", str(root),
                                 "-o", str(tmp_path / "inventory.json")])
    assert result.exit_code == 0, result.output
    (record,) = [r for r in list_runs() if r["kind"] == "discover"]
    body = client.get(f"/api/discover/{record['runId']}").json()
    assert body["project"]["root"] == "bot"
    assert body["project"]["agents"][0]["name"] == "agent"
