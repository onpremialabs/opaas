"""The run store: every result kept, written once, never replaced (D76)."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opaas.adapters import register_backend
from opaas.cli.main import app
from opaas.core.events import AuditEvent, EventLog, evidence, parse_actor_chain
from opaas.core.hashing import file_digest
from opaas.core.store import (
    RUN_FORMAT,
    StoreError,
    allocate_run_dir,
    list_runs,
    load_run,
    open_run,
    verify_files,
)
from opaas.system.orchestrate import run_audit

from .conftest import FakeBackend, ScriptedBackend

runner = CliRunner()


def _ok(result):
    assert result.exit_code == 0, result.output
    return result


def _open(**kwargs):
    return open_run("bench", command=kwargs.pop("command", ["opaas", "run"]),
                    opaas_version="9.9.9", spec_version="1.0-draft", **kwargs)


@pytest.fixture(autouse=True)
def _backends():
    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(FakeBackend)
    register_backend(Answering)


# --- The store itself -------------------------------------------------------

def test_two_runs_never_share_a_directory(_isolate_run_store):
    """Claimed with an exclusive mkdir: a taken number is skipped, never reused."""
    first = allocate_run_dir()
    (_isolate_run_store / first.name.replace("-001", "-002")).mkdir()
    second = allocate_run_dir()
    assert first.name.endswith("-001") and second.name.endswith("-003")


def test_a_file_in_a_run_is_written_once():
    run = _open()
    run.write_bytes("report.json", b"{}")
    with pytest.raises(StoreError, match="written once"):
        run.write_bytes("report.json", b"{\"replaced\": true}")
    assert (run.directory / "report.json").read_bytes() == b"{}"


def test_a_closed_run_accepts_nothing_and_its_files_are_read_only():
    run = _open()
    path = run.write_bytes("report.json", b"{}")
    record = run.close("completed")
    assert record["files"] == {"report.json": file_digest(path)}
    assert not os.access(path, os.W_OK)
    with pytest.raises(StoreError, match="closed"):
        run.write_bytes("other.json", b"{}")
    with pytest.raises(StoreError, match="closed"):
        run.close("completed")


def test_a_field_the_command_did_not_have_is_absent():
    """No endpoint, no command: absent from run.json, never defaulted."""
    run = _open(command=None)
    run.close("completed")
    record = json.loads((run.directory / "run.json").read_text(encoding="utf-8"))
    assert "endpoint" not in record and "command" not in record and "pid" not in record
    assert record["format"] == RUN_FORMAT and record["status"] == "completed"


def test_a_running_run_whose_process_is_gone_reads_interrupted():
    run = _open()
    record = json.loads((run.directory / "run.json").read_text(encoding="utf-8"))
    assert load_run(run.directory)["status"] == "running"
    record["pid"] = 2**22 + 12345  # no such process
    os.replace(run.directory / "run.json", run.directory / "old.json")
    (run.directory / "run.json").write_text(json.dumps(record), encoding="utf-8")
    assert load_run(run.directory)["status"] == "interrupted"


def test_a_record_in_an_unknown_format_is_refused_not_guessed():
    run = _open()
    run.close("completed")
    path = run.directory / "run.json"
    path.chmod(0o644)
    path.write_text(json.dumps({"format": RUN_FORMAT + 1}), encoding="utf-8")
    with pytest.raises(StoreError, match="Upgrade OPAAS"):
        load_run(run.directory)


def test_a_file_changed_after_the_run_shows_as_modified():
    run = _open()
    path = run.write_bytes("report.json", b"{}")
    run.close("completed")
    path.chmod(0o644)
    path.write_bytes(b"{\"edited\": 1}")
    assert verify_files(run.directory) == {"report.json": "modified"}
    assert runner.invoke(app, ["runs", "show", run.run_id]).exit_code == 1


# --- Commands record their runs --------------------------------------------

def _bench(*extra):
    return runner.invoke(app, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
        "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup", "0",
        "--idle-seconds", "0", "--dataset", "opaas-smoke", *extra])


def test_a_bench_run_without_output_writes_into_the_store(_isolate_run_store):
    _ok(_bench("--base-url", "http://user:pw@engine.test:8000/v1?token=abc",
               "--api-key", "sk-secret"))
    (record,) = list_runs()
    directory = _isolate_run_store / record["runId"]
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert report["runId"] == record["runId"]
    assert record["kind"] == "bench" and record["status"] == "completed"
    assert record["files"]["report.json"] == file_digest(directory / "report.json")
    # The record is as public as the report: no key, no credentials or query in a URL.
    command = " ".join(record["command"])
    assert "sk-secret" not in command and "--api-key ***" in command
    assert "user:pw@" not in command and "token=abc" not in command
    assert "--max-tokens 8" in command
    assert record["endpoint"] == "http://engine.test:8000/v1"


def test_an_existing_output_is_refused_without_force(tmp_path):
    out = tmp_path / "report.json"
    _ok(_bench("--output", str(out)))
    first = out.read_bytes()
    refused = _bench("--output", str(out))
    assert refused.exit_code == 2 and "already exists" in " ".join(refused.output.split())
    assert out.read_bytes() == first
    assert len(list_runs()) == 1


def _log(tmp_path):
    log = EventLog(tmp_path / "agent.jsonl")
    for target in ("search", "shell"):
        log.append(AuditEvent(action="tool_call", target=target,
                              actor_chain=parse_actor_chain("user:alice>agent:coder"),
                              args=evidence({}), output=evidence("")).to_record())
    return log.path


def _commands(tmp_path):
    """One invocation of every command that records a result, and the output it writes."""
    log = _log(tmp_path)
    inventory = tmp_path / "inventory.json"
    _ok(runner.invoke(app, ["discover", "scan", "--no-containers", "-o", str(inventory)]))
    topology = tmp_path / "topology.json"
    topology.write_text(json.dumps({"edges": [{"from": "user:*", "to": "agent:coder"}]}),
                        encoding="utf-8")
    report = tmp_path / "bench.json"
    _ok(_bench("--output", str(report)))
    out = tmp_path / "outputs"
    out.mkdir()
    return {
        "run": ["run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
                "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup",
                "0", "--idle-seconds", "0", "--dataset", "opaas-smoke",
                "--output", str(out / "run.json")],
        "smith": ["smith", "run", "--agent", "bare-model", "--backend", "fake-answering",
                  "--model", "m", "-n", "1", "--no-toxicity", "-o", str(out / "smith.json")],
        "discover scan": ["discover", "scan", "--no-containers", "-o", str(out / "inv.json")],
        "discover gaps": ["discover", "gaps", "-i", str(inventory), "-o", str(out / "gaps.json")],
        "inquest": ["inquest", "audit", str(log), "-o", str(out / "inquest.json")],
        "swarm": ["swarm", "analyze", str(log), "-t", str(topology),
                  "-o", str(out / "swarm.json")],
        "card build": ["card", "build", str(out.parent / "inventory.json"),
                       "-o", str(out / "card.json")],
        "card manifest": ["card", "manifest", str(inventory), "-o", str(out / "manifest.json")],
        "sentinel": ["sentinel", "overhead", "-n", "10", "-o", str(out / "overhead.json")],
        "compare": ["compare", "--report", str(report), "--report", str(report),
                    "--out", str(out / "comparison.json")],
    }


@pytest.mark.parametrize("name", ["run", "smith", "discover scan", "discover gaps", "inquest",
                                  "swarm", "card build", "card manifest", "sentinel",
                                  "compare"])
def test_no_command_can_replace_a_result_in_the_store(tmp_path, _isolate_run_store, name):
    """Run twice into the same path with --force: the first run's files stay as they were."""
    args = _commands(tmp_path)[name]
    before = {r["runId"] for r in list_runs()}
    _ok(runner.invoke(app, args))
    (first,) = [r for r in list_runs() if r["runId"] not in before]
    assert first["status"] in ("completed", "partial") and first["files"]
    _ok(runner.invoke(app, [*args, "--force"]))
    second = [r for r in list_runs() if r["runId"] not in before | {first["runId"]}]
    assert len(second) == 1
    assert verify_files(_isolate_run_store / first["runId"]) == {
        name: "ok" for name in first["files"]}


def test_runs_list_show_and_path(_isolate_run_store):
    _ok(_bench())
    (record,) = list_runs()
    listed = _ok(runner.invoke(app, ["runs", "list", "--json"]))
    assert json.loads(listed.output)[0]["runId"] == record["runId"]
    shown = _ok(runner.invoke(app, ["runs", "show", record["runId"]]))
    assert "ok" in shown.output and "report.json" in shown.output
    path = _ok(runner.invoke(app, ["runs", "path", record["runId"]]))
    assert Path(path.output.strip()) == _isolate_run_store / record["runId"]
    assert runner.invoke(app, ["runs", "show", "19700101-001"]).exit_code == 2


def test_old_runs_are_copied_in_on_request_and_never_moved(tmp_path):
    old = tmp_path / "runs" / "20260101-001"
    old.mkdir(parents=True)
    report = {"runId": "20260101-001", "startedAt": "2026-01-01T10:00:00Z",
              "finishedAt": "2026-01-01T10:30:00Z", "status": "evaluated"}
    (old / "report.json").write_text(json.dumps(report), encoding="utf-8")

    listed = _ok(runner.invoke(app, ["runs", "import", str(tmp_path / "runs")]))
    assert "would import" in listed.output and list_runs() == []

    _ok(runner.invoke(app, ["runs", "import", str(tmp_path / "runs"), "--copy"]))
    (record,) = list_runs()
    assert record["originalRunId"] == "20260101-001" and "command" not in record
    assert record["startedAt"] == "2026-01-01T10:00:00Z"
    assert record["endedAt"] == "2026-01-01T10:30:00Z"
    assert (old / "report.json").is_file()


def test_an_audit_never_counts_a_document_left_by_an_earlier_one(tmp_path):
    """With --force into a used directory, a step that writes nothing has failed (D76)."""
    from .test_system_audit import _system

    out = tmp_path / "out"
    out.mkdir()
    (out / "inventory.json").write_text("{}", encoding="utf-8")
    calls = []

    def invoke(args):
        calls.append(args)
        return 2

    run = run_audit(_system(tmp_path), base=tmp_path, out=out, invoke=invoke, force=True,
                    only=["discover"])
    assert all(args[-1] == "--force" for args in calls)
    discover = [s for s in run.steps if s.step == "discover"]
    assert discover and all(s.status == "failed" for s in discover)


def test_the_recorded_command_names_opaas_and_its_subcommands(tmp_path):
    out = tmp_path / "overhead.json"
    _ok(runner.invoke(app, ["sentinel", "overhead", "-n", "10", "-o", str(out)]))
    (record,) = list_runs()
    assert record["command"][:3] == ["opaas", "sentinel", "overhead"]
    assert record["command"][3:] == ["--requests", "10", "--out", str(out)]
