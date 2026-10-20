"""Every long command writes its progress into its run (phase 20, docs/dashboard.md §4)."""

import json

import pytest
from typer.testing import CliRunner

from opaas.adapters import register_backend
from opaas.cli.main import app
from opaas.core.store import (
    PROGRESS_FIELDS,
    PROGRESS_FILE,
    StoreError,
    list_runs,
    open_run,
    read_progress,
    runs_dir,
)

from .conftest import FakeBackend, ScriptedBackend

runner = CliRunner()


def _ok(result):
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture(autouse=True)
def _backends():
    class Answering(ScriptedBackend):
        name = "fake-answering"

        def __init__(self):
            super().__init__(lambda prompt: '{"final": "I cannot tell."}')

    register_backend(FakeBackend)
    register_backend(Answering)


def _lines(record):
    path = runs_dir() / record["runId"] / PROGRESS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_bench_run_writes_its_blocks_progress_from_start_to_end():
    _ok(runner.invoke(app, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
        "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup", "0",
        "--idle-seconds", "0", "--dataset", "opaas-smoke", "--suite", "performance",
        "--suite", "capability"]))
    (record,) = list_runs()
    lines = _lines(record)
    assert lines[0]["kind"] == "start" and lines[-1] == {**lines[-1], "kind": "end",
                                                          "message": "ended completed"}
    assert {line.get("phase") for line in lines} >= {"performance", "capability"}
    # Not part of the run's files: it is a log, not a result.
    assert PROGRESS_FILE not in record["files"]


def test_a_smith_run_writes_one_line_per_scenario_at_most(tmp_path):
    _ok(runner.invoke(app, ["smith", "run", "--agent", "bare-model", "--backend",
                            "fake-answering", "--model", "m", "-n", "1", "--no-toxicity",
                            "-s", "pack:banking", "-o", str(tmp_path / "smith.json")]))
    (record,) = list_runs()
    scenarios = [line for line in _lines(record) if line.get("phase") == "scenarios"]
    assert scenarios and scenarios[-1]["percent"] == 100.0


def test_no_progress_line_carries_content(tmp_path):
    """N4: secrets, URL credentials and query strings never reach a progress line."""
    secret = "sk-progress-secret"
    _ok(runner.invoke(app, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
        "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup", "0",
        "--idle-seconds", "0", "--api-key", secret,
        "--base-url", "http://user:pw@engine.test:8000/v1?token=abc"]))
    _ok(runner.invoke(app, ["discover", "scan", "--no-containers",
                            "-o", str(tmp_path / "inventory.json")]))
    for record in list_runs():
        for line in _lines(record):
            assert set(line) <= PROGRESS_FIELDS
            text = json.dumps(line)
            assert secret not in text and "user:pw@" not in text and "token=abc" not in text


def test_a_line_with_another_field_is_refused_and_a_url_is_stripped():
    run = open_run("bench", command=None, opaas_version="9", spec_version="x")
    with pytest.raises(StoreError, match="carry no"):
        run.append_progress(message="x", prompt="what the user asked")
    run.append_progress(message="GET http://a:b@host:1/x?key=v done")
    (line,), _ = read_progress(run.directory)
    assert line["message"] == "GET http://host:1/x done"


def test_a_reader_follows_the_log_and_never_reads_half_a_line():
    run = open_run("bench", command=None, opaas_version="9", spec_version="x")
    run.append_progress(kind="info", message="one")
    first, offset = read_progress(run.directory)
    with (run.directory / PROGRESS_FILE).open("a", encoding="utf-8") as handle:
        handle.write('{"t": "x", "message": "half')
    second, again = read_progress(run.directory, offset=offset)
    assert [line["message"] for line in first] == ["one"]
    assert second == [] and again == offset


def test_progress_is_not_writable_as_a_file():
    run = open_run("bench", command=None, opaas_version="9", spec_version="x")
    with pytest.raises(StoreError, match="own record"):
        run.write_bytes(PROGRESS_FILE, b"{}")
