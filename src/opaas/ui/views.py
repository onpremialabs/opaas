"""What the dashboard's pages show, built from files and nothing else (D79).

Every function here reads a run directory or the mascot grid and returns plain
data for a template. None of them measures, scores or ranks: a figure is
shown per category with its interval, in the order the report gives it, and
what was not measured is shown as not measured, with its reason (invariants 1
and 2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opaas.core.store import RUN_FILE, list_runs, load_run, read_progress, verify_files

#: Where a run keeps the report the pages summarise, in order of preference.
REPORTS = ("report.json", "system-audit/system-report.json")

#: The meerkat's head: the top rows and left columns of the CLI's grid.
HEAD_ROWS, HEAD_COLS = 14, 22


def runs(store: Path) -> list[dict[str, Any]]:
    """Every run, newest first, with how many drafts each has."""
    records = list_runs(store)
    drafts: dict[str, int] = {}
    for record in records:
        if record.get("kind") == "certificate" and record.get("sourceRun"):
            drafts[record["sourceRun"]] = drafts.get(record["sourceRun"], 0) + 1
    for record in records:
        record["draftCount"] = drafts.get(record["runId"], 0)
    return list(reversed(records))


def run_page(store: Path, run_id: str) -> dict[str, Any]:
    """Everything the run page shows: record, files checked, progress, report, drafts."""
    directory = store / run_id
    record = load_run(directory)
    checks = verify_files(directory) if record.get("status") != "running" else {}
    progress, _ = read_progress(directory)
    report_name = next((n for n in REPORTS if n in record.get("files", {})), None)
    drafts = sorted(
        (r for r in list_runs(store)
         if r.get("kind") == "certificate" and r.get("sourceRun") == run_id),
        key=lambda r: r.get("version", 0), reverse=True)
    return {
        "record": record,
        "files": [{"name": n, "digest": d, "check": checks.get(n, "unchecked")}
                  for n, d in record.get("files", {}).items()],
        "progress": progress[-40:],
        "report": _report(directory / report_name) if report_name else None,
        "drafts": drafts,
    }


def _report(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("kind") == "system-report":
        return {"kind": "system", "coverage": payload.get("coverage", {}),
                "limitations": payload.get("limitations", [])}
    blocks = []
    for suite in payload.get("suites", []):
        metrics = suite.get("metrics", {})
        blocks.append({
            "block": suite.get("block"),
            "suite": suite.get("suite"),
            "status": suite.get("status"),
            "error": suite.get("error"),
            "rows": _rows(suite.get("suite"), metrics),
            "notMeasured": _not_measured(metrics),
            "limitations": suite.get("limitations", []),
        })
    coverage = payload.get("coverage", {})
    model = payload.get("model")
    name = model.get("name") if isinstance(model, dict) else model
    quantization = model.get("quantization") if isinstance(model, dict) else None
    return {"kind": "model", "status": payload.get("status"), "model": name,
            "quantization": quantization,
            "blocks": blocks, "missing": coverage.get("blocksMissing", []),
            "limitations": payload.get("limitations", [])}


def _rows(suite: str | None, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Interval rows, in the report's order. Only figures that carry an interval."""
    rows = []
    if suite == "capability":
        for task in metrics.get("tasks", []):
            rows.append(_interval(task.get("category") or task.get("name"),
                                  task.get("accuracy")))
    elif suite == "safety":
        for task in metrics.get("tasks", []):
            for key, value in task.items():
                if isinstance(value, dict) and {"point", "low", "high"} <= set(value):
                    rows.append(_interval(f"{task.get('name')} · {key}", value))
    elif suite == "consistency":
        determinism = metrics.get("determinism", {})
        if isinstance(determinism.get("identicalRate"), dict):
            rows.append(_interval("determinism · identical", determinism["identicalRate"]))
    return [row for row in rows if row is not None]


def _interval(label: Any, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("point") is None:
        return None
    return {"label": str(label), "point": value["point"], "low": value.get("low"),
            "high": value.get("high"), "n": value.get("n")}


def _not_measured(metrics: dict[str, Any]) -> list[dict[str, str]]:
    items = []
    for entry in metrics.get("notImplemented", []):
        if isinstance(entry, dict):
            items.append({"what": str(entry.get("dimension", "")),
                          "why": str(entry.get("reason", ""))})
        else:
            items.append({"what": str(entry), "why": ""})
    return items


def run_for_job(store: Path, job_id: str) -> dict[str, Any] | None:
    """The run a dashboard job produced, found by the job id its record carries."""
    if not store.is_dir():
        return None
    for directory in sorted(store.iterdir(), reverse=True):
        path = directory / RUN_FILE
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if record.get("jobId") == job_id:
            return load_run(directory)
    return None


def meerkat() -> dict[str, Any]:
    """The mascot grid of the CLI, for the pages to draw (dashboard.md §7, *The meerkat*)."""
    from opaas.cli.sprite import MASCOT, PALETTE

    return {"rows": list(MASCOT), "palette": dict(PALETTE)}


def head_svg() -> str:
    """The meerkat's head as an SVG: the favicon and the header mark."""
    data = meerkat()
    cells = []
    for y, row in enumerate(data["rows"][:HEAD_ROWS]):
        for x, ch in enumerate(row[:HEAD_COLS]):
            if ch != ".":
                cells.append(f'<rect x="{x}" y="{y}" width="1" height="1" '
                             f'fill="{data["palette"][ch]}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -4 {HEAD_COLS} {HEAD_COLS}" '
            f'shape-rendering="crispEdges">{"".join(cells)}</svg>')
