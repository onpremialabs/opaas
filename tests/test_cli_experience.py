"""How the CLI talks: help grouped by task, links instead of decision numbers,
one look for status lines and errors, and what to run next."""

import re
from pathlib import Path

import pytest
import typer
from rich.console import Console
from typer.main import get_command
from typer.testing import CliRunner

from opaas.cli import common, docs
from opaas.cli.main import app

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _anchors(path: Path) -> set[str]:
    """GitHub's heading anchors: lowercase, punctuation dropped, spaces to hyphens."""
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6} (.*)$", line)
        if match:
            text = re.sub(r"[^\w\- ]", "", match.group(1).strip().lower())
            anchors.add(text.replace(" ", "-"))
    return anchors


def _walk(command, path="opaas"):
    yield path, command
    for name, sub in getattr(command, "commands", {}).items():
        yield from _walk(sub, f"{path} {name}")


# ------------------------------------------------------------------- links


@pytest.mark.parametrize("topic", sorted(docs.DOCS))
def test_every_documentation_link_points_at_a_file_and_heading_that_exist(topic):
    path, anchor = docs.DOCS[topic]
    assert (ROOT / path).is_file(), path
    if anchor:
        assert anchor in _anchors(ROOT / path), f"{path} has no heading #{anchor}"


@pytest.mark.parametrize("number", sorted(docs.DECISIONS))
def test_every_decision_link_points_at_its_heading(number):
    anchor = docs.DECISIONS[number]
    assert anchor.startswith(f"d{number}--")
    assert anchor in _anchors(ROOT / "docs" / "decisions.md")


def test_links_point_at_the_canonical_repository():
    assert docs.doc("guide").startswith("https://github.com/onpremialabs/opaas/blob/develop/")


# -------------------------------------------------------------------- help


def test_the_help_groups_commands_by_task_in_the_order_a_newcomer_needs_them():
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0
    panels = ["Audit", "Results", "Data and setup", "Signed credentials (advanced)"]
    positions = [result.output.index(f"─ {panel} ") for panel in panels]
    assert positions == sorted(positions)
    assert "─ Commands " not in result.output, "a command was left out of every panel"


def test_every_top_level_command_sits_in_a_panel():
    root = get_command(app)
    loose = [name for name, command in root.commands.items()
             if not getattr(command, "rich_help_panel", None)]
    assert loose == []


def test_signing_is_kept_apart_from_the_audit_flow():
    root = get_command(app)
    signed = {name for name, command in root.commands.items()
              if command.rich_help_panel == docs.PANEL_SIGNED}
    assert {"keygen", "sign", "verify", "delegate", "revoke", "root-document"} <= signed
    assert root.commands["certificate"].rich_help_panel == docs.PANEL_RESULTS


def test_no_help_text_cites_a_decision_by_number():
    """"(D65)" tells a newcomer nothing; the epilog links the decision instead."""
    cited = []
    for path, command in _walk(get_command(app)):
        texts = [command.help or ""] + [getattr(p, "help", None) or "" for p in command.params]
        cited += [(path, m) for text in texts for m in re.findall(r"\bD\d+\b", text)]
    assert cited == []


def test_every_top_level_command_links_its_documentation():
    for name, command in get_command(app).commands.items():
        assert "Docs: https://github.com/onpremialabs/opaas/" in (command.epilog or ""), name


def test_a_commands_help_ends_with_its_documentation_and_the_decision_behind_it():
    result = runner.invoke(app, ["sign", "--help"], env={"COLUMNS": "200"})
    assert "Docs: https://github.com/onpremialabs/opaas/blob/develop/docs/guide.md" in result.output
    assert "Why it works this way:" in result.output
    assert "decisions.md#d65--" in result.output


# ------------------------------------------------------------ status lines


def _stderr(monkeypatch, call) -> str:
    console = Console(width=200, no_color=True, emoji=False)
    monkeypatch.setattr(common, "err_console", console)
    with console.capture() as capture:
        call()
    return capture.get()


def test_an_error_says_what_happened_then_what_to_do(monkeypatch):
    out = _stderr(monkeypatch, lambda: common.error(
        "[red]report.json already exists.[/red] Choose another path, or pass --force."))
    assert out.splitlines() == ["✗ report.json already exists.",
                                "  → Choose another path, or pass --force."]


def test_a_one_part_error_is_split_at_its_first_sentence(monkeypatch):
    out = _stderr(monkeypatch, lambda: common.error(
        "[red]No run 20260101-001 in /runs. `opaas runs list` shows the ones there are.[/red]"))
    assert out.splitlines() == ["✗ No run 20260101-001 in /runs.",
                                "  → `opaas runs list` shows the ones there are."]


def test_a_label_ending_in_a_colon_stays_with_its_detail(monkeypatch):
    out = _stderr(monkeypatch, lambda: common.error("[red]Backend error:[/red] refused"))
    assert out.splitlines() == ["✗ Backend error: refused"]


def test_an_error_can_name_what_to_do_and_where_to_read(monkeypatch):
    out = _stderr(monkeypatch, lambda: common.error(
        "The dashboard needs the ui extra.", "pip install \"opaas[ui]\"",
        docs=docs.doc("dashboard")))
    lines = out.splitlines()
    assert lines[0] == "✗ The dashboard needs the ui extra."
    assert lines[1] == "  → pip install \"opaas[ui]\""
    assert lines[2].startswith("  Docs: https://github.com/onpremialabs/opaas/")


def test_a_message_with_brackets_that_are_not_markup_is_still_printed(monkeypatch):
    out = _stderr(monkeypatch, lambda: common.error("bad value [x=1"))
    assert "✗ bad value [x=1" in out


def test_warnings_and_successes_carry_their_mark(monkeypatch):
    console = Console(width=120, no_color=True)
    with console.capture() as capture:
        common.warning("partial evaluation", out=console)
        common.success("Credential signed.", out=console)
    assert capture.get().splitlines() == ["! partial evaluation", "✓ Credential signed."]


def test_a_cli_error_reaches_the_user_in_two_parts():
    result = runner.invoke(app, ["show", "missing-report.json"])
    assert result.exit_code == 2
    assert "✗ Does not exist: missing-report.json" in result.output


# -------------------------------------------------------------- next steps


def test_an_audit_run_suggests_its_certificate_first():
    steps = common.run_next_steps("bench", "20261020-001")
    assert steps[0][0] == "opaas certificate --run 20261020-001"
    assert any(command == "opaas ui" for command, _ in steps)


def test_a_run_without_a_report_does_not_offer_a_certificate():
    for kind in ("discover", "card", "inquest", "sentinel", "swarm", "certificate", "compare"):
        assert not any("certificate" in command
                       for command, _ in common.run_next_steps(kind, "20261020-001")), kind


def test_discovery_leads_to_a_system_manifest():
    commands = [command for command, _ in common.run_next_steps("discover", "20261020-002")]
    assert "opaas audit draft -o system.json" in commands


def test_a_recorded_run_ends_with_what_to_run_next(tmp_path):
    probe = typer.Typer()

    @probe.command()
    def measure(ctx: typer.Context) -> None:
        with common.recorded_run(ctx, "smith"):
            pass

    @probe.command()
    def other() -> None:
        pass

    result = runner.invoke(probe, ["measure"])
    assert result.exit_code == 0, result.output
    run_id = re.search(r"Recorded as run (\S+)", result.output).group(1)
    assert result.output.index("✓ Recorded as run") < result.output.index("Next:")
    assert f"opaas certificate --run {run_id}" in result.output
    assert f"opaas runs show {run_id}" in result.output
