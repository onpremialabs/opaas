"""Versioned audit certificates of stored runs (D77, D81)."""

import json

import pytest
from typer.testing import CliRunner

from opaas.adapters import register_backend
from opaas.cli.main import app
from opaas.core.hashing import file_digest
from opaas.core.store import list_runs, runs_dir

from .conftest import FakeBackend

runner = CliRunner()


def _ok(result):
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture(autouse=True)
def _fake():
    register_backend(FakeBackend)


def _bench() -> str:
    _ok(runner.invoke(app, [
        "run", "--model", "fake/model-1b", "--backend", "fake", "--concurrency", "1",
        "--prompt-words", "32", "--max-tokens", "8", "--requests", "2", "--warmup", "0",
        "--idle-seconds", "0", "--dataset", "opaas-smoke"]))
    (record,) = [r for r in list_runs() if r["kind"] == "bench"]
    return record["runId"]


def _certificates(source):
    return sorted((r for r in list_runs()
                   if r["kind"] == "certificate" and r.get("sourceRun") == source),
                  key=lambda r: r["version"])


def test_each_certificate_is_a_new_version_and_none_replaces_another():
    source = _bench()
    _ok(runner.invoke(app, ["certificate", "--run", source]))
    _ok(runner.invoke(app, ["certificate", "--run", source]))
    first, second = _certificates(source)
    assert (first["version"], second["version"]) == (1, 2)
    assert set(first["files"]) == {"certificate-v1.html", "certificate-v1.json"}
    assert set(second["files"]) == {"certificate-v2.html", "certificate-v2.json"}
    page = (runs_dir() / first["runId"] / "certificate-v1.html").read_text(encoding="utf-8")
    assert "Certificate of Evaluation" in page
    assert "Generated with OPAAS by onpremIA Labs" in page
    # Its seal says who answers for it: whoever ran the audit (D81).
    assert ">Self-attested audit certificate</p>" in page
    assert "Attested by whoever ran the audit" in page
    # The final certificate: no draft mark anywhere, and checked by the report's hash.
    assert "Draft" not in page and "DRAFT" not in page and "not issued" not in page
    assert "sha256sum report.json" in page
    assert file_digest(runs_dir() / source / "report.json") in page
    assert "<svg" not in page  # no QR code without a base URL


def test_a_certificate_records_what_it_was_rendered_from():
    source = _bench()
    _ok(runner.invoke(app, ["certificate", "--run", source]))
    (certificate,) = _certificates(source)
    meta = json.loads((runs_dir() / certificate["runId"] / "certificate-v1.json")
                      .read_text(encoding="utf-8"))
    report = runs_dir() / source / "report.json"
    assert meta["reportHash"] == file_digest(report)
    assert meta["signed"] is False and meta["sourceRun"] == source
    assert meta["templateHash"].startswith("sha256:")


def test_no_certificate_is_rendered_from_a_report_edited_after_its_run():
    source = _bench()
    report = runs_dir() / source / "report.json"
    report.chmod(0o644)
    report.write_text(report.read_text(encoding="utf-8").replace("fake", "real"),
                      encoding="utf-8")
    result = runner.invoke(app, ["certificate", "--run", source])
    assert result.exit_code == 1 and _certificates(source) == []


def test_a_run_without_a_report_has_no_certificate(tmp_path):
    _ok(runner.invoke(app, ["discover", "scan", "--no-containers",
                            "-o", str(tmp_path / "inventory.json")]))
    (record,) = list_runs()
    assert runner.invoke(app, ["certificate", "--run", record["runId"]]).exit_code == 2


def test_a_signed_certificate_still_needs_both_report_and_credential(tmp_path):
    result = runner.invoke(app, ["certificate", "--report", str(tmp_path / "r.json")])
    assert result.exit_code == 2
    assert "--run" in " ".join(result.output.split())
