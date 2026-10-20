"""`opaas card`: the Auditability Card and the deployment manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from opaas.card import ManifestError, build_card, build_manifest, render_card
from opaas.cli.common import (
    Force,
    _plain,
    console,
    error,
    read_json,
    recorded_run,
    refuse_overwrite,
)
from opaas.core.evidence import EvidenceError, envelope, write_document

app = typer.Typer(help="Auditability Card (Q1–Q6) and deployment manifest.",
                  no_args_is_help=True)


@app.command("build")
def build_command(
    ctx: typer.Context,
    evidence: Annotated[list[Path], typer.Argument(help="Evidence documents to answer from.")],
    retention: Annotated[Path | None, typer.Option(
        "--retention", help="Operator declaration: keptFields, retentionDays (N4).")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("card.json"),
    html: Annotated[Path | None, typer.Option("--html", help="Printable rendering.")] = None,
    force: Force = False,
) -> None:
    """Fills Q1–Q6 from evidence; every answer cites its file and hash."""
    try:
        card = build_card(evidence, retention=read_json(retention) if retention else None)
    except (EvidenceError, OSError, ValueError) as exc:
        error(f"[red]{_plain(exc)}[/red]")
        raise typer.Exit(code=2) from None
    refuse_overwrite(out, force)
    refuse_overwrite(html, force)
    with recorded_run(ctx, "card") as recorded:
        digest = write_document(out, envelope("auditability-card", card, environment=None))
        recorded.keep(out)
        if html is not None:
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text(render_card(card), encoding="utf-8")
            recorded.keep(html)
    for qid, entry in card["questions"].items():
        state = (f"{len(entry['answers'])} answer(s)" if entry["answers"]
                 else "[yellow]not answered[/yellow]")
        gaps = f", [red]{len(entry['gaps'])} gap(s)[/red]" if entry["gaps"] else ""
        console.print(f"  {qid} {entry['dimension']:<26} {state}{gaps}")
    if html is not None:
        console.print(f"  rendering: {html}")
    console.print(f"\n{out} ({digest}). Sign it with `opaas evidence sign`.")


@app.command("manifest")
def manifest_command(
    ctx: typer.Context,
    members: Annotated[list[Path], typer.Argument(help="Reports and evidence documents.")],
    name: Annotated[str | None, typer.Option("--name", help="Deployment name.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("manifest.json"),
    force: Force = False,
) -> None:
    """Lists every report behind a deployment by hash; checks they share one environment."""
    try:
        manifest = build_manifest(members, name=name)
    except ManifestError as exc:
        error(f"[red]{_plain(exc)}[/red]")
        raise typer.Exit(code=2) from None
    refuse_overwrite(out, force)
    with recorded_run(ctx, "card") as recorded:
        digest = write_document(out, envelope("deployment-manifest", manifest,
                                              environment=None))
        recorded.keep(out)
    for member in manifest["members"]:
        console.print(f"  {member['kind']:<20} {member['file']}  {member['hash'][:23]}…")
    for finding in manifest["findings"]:
        console.print(f"  [yellow]{_plain(finding)}[/yellow]")
    console.print(f"\n{out} ({digest}). Sign it with `opaas evidence sign`.")
