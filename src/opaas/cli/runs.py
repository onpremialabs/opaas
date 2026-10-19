"""`opaas runs`: the run store, read-only (D76).

Every command that produces a result records it in the store
(`~/.opaas/runs`, or `$OPAAS_RUNS_DIR`). These commands only look: they list
runs, show one with its files checked against the hashes it recorded, print
where it lives, and copy old `./runs` directories in. None of them changes or
deletes a run; removing one is done by hand, outside OPAAS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from opaas import OPAAS_SPEC_VERSION, __version__
from opaas.cli.common import _plain, console, err_console
from opaas.core.store import (
    RUN_FILE,
    StoreError,
    import_directory,
    list_runs,
    load_run,
    runs_dir,
    verify_files,
)

app = typer.Typer(help="The run store: every result kept, never replaced.",
                  no_args_is_help=True)

RunId = Annotated[str, typer.Argument(help="Run id, e.g. 20261019-003.")]

_STATUS_STYLE = {"completed": "green", "partial": "yellow", "failed": "red",
                 "running": "cyan", "interrupted": "magenta"}


def _run_directory(run_id: str) -> Path:
    directory = runs_dir() / run_id
    if not (directory / RUN_FILE).is_file():
        err_console.print(f"[red]No run {_plain(run_id)} in {runs_dir()}.[/red] "
                          "`opaas runs list` shows the ones there are.")
        raise typer.Exit(code=2)
    return directory


@app.command("list")
def list_command(
    kind: Annotated[str | None, typer.Option("--kind", help="Only runs of this kind.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Every recorded run, oldest first."""
    runs = [r for r in list_runs() if kind is None or r.get("kind") == kind]
    if as_json:
        print(json.dumps(runs, indent=2, ensure_ascii=False))
        return
    if not runs:
        console.print(f"No runs in {runs_dir()} yet.")
        return
    table = Table("run", "kind", "status", "started", "opaas", "command")
    for record in runs:
        status = str(record.get("status"))
        style = _STATUS_STYLE.get(status, "white")
        command = " ".join(record.get("command", [])) or (
            f"imported from {record['importedFrom']}" if "importedFrom" in record else "—")
        table.add_row(record["runId"], str(record.get("kind")),
                      f"[{style}]{status}[/{style}]", str(record.get("startedAt", "—")),
                      str(record.get("opaasVersion", "—")), _plain(command))
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s) in {runs_dir()}[/dim]")


@app.command("show")
def show_command(run_id: RunId) -> None:
    """One run's record, with every file checked against the hash it recorded."""
    directory = _run_directory(run_id)
    try:
        record = load_run(directory)
        checks = verify_files(directory)
    except (StoreError, ValueError) as exc:
        err_console.print(f"[red]{_plain(exc)}[/red]")
        raise typer.Exit(code=1) from None
    for key in ("runId", "kind", "status", "opaasVersion", "specVersion", "startedAt",
                "endedAt", "endpoint", "importedFrom"):
        if key in record:
            console.print(f"{key:<13} {_plain(record[key])}")
    if "command" in record:
        console.print(f"{'command':<13} {_plain(' '.join(record['command']))}")
    style = {"ok": "green", "modified": "red", "missing": "red"}
    for name, digest in record.get("files", {}).items():
        state = checks.get(name, "missing")
        console.print(f"  [{style[state]}]{state:<8}[/{style[state]}] {_plain(name)}  "
                      f"[dim]{digest}[/dim]")
    if any(state != "ok" for state in checks.values()):
        err_console.print("[red]Some files no longer match the hash this run recorded.[/red]")
        raise typer.Exit(code=1)


@app.command("path")
def path_command(run_id: RunId) -> None:
    """Prints the run's directory, for scripts."""
    print(_run_directory(run_id))


@app.command("import")
def import_command(
    source: Annotated[Path, typer.Argument(
        help="A directory of old runs, as `opaas run` wrote them before the store.")]
    = Path("runs"),
    copy: Annotated[bool, typer.Option(
        "--copy", help="Copy them in. Without it, only list what would be imported.")]
    = False,
) -> None:
    """Copies runs written before the store into it. The originals are never moved."""
    candidates = sorted(
        d for d in (source.iterdir() if source.is_dir() else [])
        if d.is_dir() and (d / "report.json").is_file() and not (d / RUN_FILE).exists()
    )
    if not candidates:
        console.print(f"Nothing to import from {source}.")
        return
    for directory in candidates:
        fields, status = _imported_fields(directory / "report.json")
        if not copy:
            console.print(f"  would import {directory} ({status})")
            continue
        try:
            run = import_directory(directory, kind="bench", opaas_version=__version__,
                                   spec_version=OPAAS_SPEC_VERSION, fields=fields,
                                   status=status)
        except (StoreError, OSError) as exc:
            err_console.print(f"[red]{directory}: {_plain(exc)}[/red]")
            raise typer.Exit(code=1) from None
        console.print(f"  {directory} → run {run.run_id}")
    if not copy:
        console.print("[dim]Pass --copy to import them. The originals stay where they are.[/dim]")


def _imported_fields(report: Path) -> tuple[dict[str, Any], str]:
    """What an old report says about its run: its id, its times, how it ended.

    The command that produced it is not known, and is left absent rather than
    guessed from the report's configuration (`docs/dashboard.md` §10).
    """
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "completed"
    fields: dict[str, Any] = {}
    if payload.get("runId"):
        fields["originalRunId"] = payload["runId"]
    if payload.get("startedAt"):
        fields["startedAt"] = payload["startedAt"]
    if payload.get("finishedAt"):
        fields["endedAt"] = payload["finishedAt"]
    return fields, "partial" if payload.get("status") == "partial" else "completed"
