"""Updating a system manifest from a new scan, and the system's folder dialog (D86)."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from opaas.cli.main import app as cli
from opaas.system.draft import PURPOSE_PLACEHOLDER, update_manifest
from opaas.ui.dialog import DialogError, dialog_command, pick_folder

runner = CliRunner()


def _said(result):
    """The output as one line: Rich wraps at the terminal's width, and a long temporary
    path (CI's are) moves where a phrase breaks."""
    return " ".join(result.output.split())


def _model(cid, name):
    return {"id": cid, "kind": "model", "backend": "ollama", "model": name,
            "base_url": "http://127.0.0.1:11434"}


def test_an_update_adds_what_is_new_and_keeps_everything_a_person_wrote():
    existing = {"format": 1, "id": "support", "purpose": "Answers employees.",
                "components": [_model("llm", "qwen2.5:7b"), _model("old", "gone:1b"),
                               {"id": "kb", "kind": "tool", "name": "search"}],
                "flows": [{"from": "llm", "to": "kb"}], "thresholds": "t.json"}
    drafted = {"format": 1, "id": "system", "purpose": PURPOSE_PLACEHOLDER,
               "components": [_model("qwen2-5-7b", "qwen2.5:7b"), _model("llm", "mistral:7b")],
               "flows": []}
    manifest, notes = update_manifest(existing, drafted)
    assert manifest["purpose"] == "Answers employees." and manifest["id"] == "support"
    assert manifest["flows"] == existing["flows"] and manifest["thresholds"] == "t.json"
    ids = [c["id"] for c in manifest["components"]]
    # The same model is not added twice; a new one whose id is taken gets another.
    assert ids == ["llm", "old", "kb", "llm-2"]
    assert "Added model llm-2: newly observed." in notes
    assert any(n.startswith("Kept model old") for n in notes)


def test_an_update_writes_a_purpose_only_where_there_was_none():
    existing = {"format": 1, "id": "s", "purpose": PURPOSE_PLACEHOLDER,
                "components": [_model("llm", "a")]}
    drafted = {"format": 1, "id": "s", "purpose": "Triage.", "components": [_model("llm", "a")]}
    manifest, notes = update_manifest(existing, drafted)
    assert manifest["purpose"] == "Triage."
    assert notes[0].startswith("Nothing new observed")


def test_audit_draft_updates_an_existing_manifest_and_refuses_without_saying_how(tmp_path):
    root = tmp_path / "bot"
    root.mkdir()
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"files": {"command": "npx"}}}))
    out = root / "system.json"
    first = runner.invoke(cli, ["audit", "draft", "--project", str(root), "-o", str(out),
                                "--purpose", "Answers employees."])
    assert first.exit_code == 0, first.output
    written = json.loads(out.read_text())
    written["components"].append({"id": "llm", "kind": "model", "backend": "ollama",
                                  "model": "qwen2.5:7b"})
    out.write_text(json.dumps(written))
    refused = runner.invoke(cli, ["audit", "draft", "--project", str(root), "-o", str(out)])
    assert refused.exit_code == 2 and "--update" in _said(refused)
    updated = runner.invoke(cli, ["audit", "draft", "--project", str(root), "-o", str(out),
                                  "--update"])
    assert updated.exit_code == 0, updated.output
    after = json.loads(out.read_text())
    assert after["purpose"] == "Answers employees."
    assert [c["id"] for c in after["components"]] == ["files", "llm"]
    assert "Nothing new observed" in _said(updated)


# --- The system's folder dialog ----------------------------------------------

def test_each_platform_opens_its_own_folder_dialog():
    mac = dialog_command("/Users/me", platform="darwin")
    assert mac[0] == "osascript" and 'POSIX file "/Users/me"' in mac[2]
    assert dialog_command("/home/me", platform="linux", which=lambda n: n == "zenity")[0] \
        == "zenity"
    assert dialog_command("/home/me", platform="linux",
                          which=lambda n: n == "kdialog")[:2] == ["kdialog",
                                                                  "--getexistingdirectory"]
    tk = dialog_command(r"C:\work", platform="win32")
    assert "askdirectory" in tk[3] and tk[4] == r"C:\work"


def _done(code, out="", err=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr=err)


def test_a_chosen_folder_comes_back_in_this_systems_form_and_a_cancel_is_none():
    assert pick_folder("x", run=lambda *a, **k: _done(0, "C:/work/bot\n")) is not None
    assert pick_folder("x", run=lambda *a, **k: _done(0, "")) is None
    assert pick_folder("x", platform="darwin",
                       run=lambda *a, **k: _done(1, err="execution error: User canceled. (-128)")
                       ) is None
    assert pick_folder("x", platform="linux", which=lambda n: n == "zenity",
                       run=lambda *a, **k: _done(1)) is None


def test_no_desktop_is_said_not_faked():
    with pytest.raises(DialogError, match="no display"):
        pick_folder("x", run=lambda *a, **k: _done(1, err="_tkinter.TclError: no display"))

    def missing(*args, **kwargs):
        raise FileNotFoundError

    with pytest.raises(DialogError, match="not installed"):
        pick_folder("x", platform="darwin", run=missing)
