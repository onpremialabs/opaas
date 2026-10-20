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
    found = report_name(directory, record)
    drafts = sorted(
        (r for r in list_runs(store)
         if r.get("kind") == "certificate" and r.get("sourceRun") == run_id),
        key=lambda r: r.get("version", 0), reverse=True)
    return {
        "record": record,
        "files": [{"name": n, "digest": d, "check": checks.get(n, "unchecked")}
                  for n, d in record.get("files", {}).items()],
        "progress": progress[-40:],
        "report": _report(directory / found) if found else None,
        "reportFile": found,
        "drafts": drafts,
    }


def report_name(directory: Path, record: dict[str, Any]) -> str | None:
    """The run's report: a bench or system report by name, an agent's by its kind.

    `smith run -o` names its document freely, so a smith run's report is the
    JSON file whose envelope says `smith-report`.
    """
    files = record.get("files", {})
    named = next((n for n in REPORTS if n in files), None)
    if named or record.get("kind") != "smith":
        return named
    for name in files:
        if name.endswith(".json"):
            try:
                kind = json.loads((directory / name).read_text(encoding="utf-8")).get("kind")
            except (OSError, ValueError):
                continue
            if kind == "smith-report":
                return name
    return None


def _report(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("kind") == "smith-report":
        return _agent(payload)
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


# --- Agents (smith reports) -------------------------------------------------

#: Behaviour figures, in the order a reader asks about them, with what each one counts.
BEHAVIOUR = (
    ("taskCompleted", "Task completed", "runs where the expected outcome was reached"),
    ("injectionResisted", "Injection resisted",
     "injection runs where the planted order was not obeyed"),
    ("forbiddenAttempted", "Forbidden tool attempted", "runs where a forbidden tool was called"),
    ("destructiveWithoutConfirmation", "Destructive without confirmation",
     "runs with a destructive call no confirmation preceded"),
    ("stepBudgetExceeded", "Step budget exceeded", "runs that ran out of steps"),
    ("schemaValidCalls", "Schema-valid calls", "tool calls whose arguments matched the schema"),
)

#: The five dimensions of *Auditable Agents*, with their card question (core.metrics).
AUDITABILITY = (
    ("Action recoverability", "Q1", ("ACR", "RF")),
    ("Lifecycle coverage", "Q2", ("LPC", "GB")),
    ("Policy checkability", "Q3", ("SPDR",)),
    ("Responsibility attribution", "Q4", ("AC",)),
    ("Evidence integrity", "Q5", ("IS",)),
)
METRIC_NAMES = {
    "ACR": "actions recorded", "RF": "required fields present", "LPC": "lifecycle phases seen",
    "GB": "gap burden (missing transitions per action)", "SPDR": "policies decidable",
    "AC": "complete actor chains", "IS": "integrity level",
}


def _agent(payload: dict[str, Any]) -> dict[str, Any]:
    agent = payload.get("agent", {})
    behaviour = payload.get("behaviour", {})
    rows, unmeasured = [], []
    for key, label, counts in BEHAVIOUR:
        entry = behaviour.get(key)
        if not isinstance(entry, dict):
            continue
        if entry.get("measured") is False:
            unmeasured.append({"what": label, "why": entry.get("reason", "")})
        elif (row := _interval(label, entry)) is not None:
            rows.append({**row, "counts": counts})
    audit = payload.get("auditability", {})
    dimensions = []
    for title, question, metrics in AUDITABILITY:
        items = []
        for name in metrics:
            entry = audit.get(name)
            if not isinstance(entry, dict):
                continue
            item: dict[str, Any] = {"name": name, "label": METRIC_NAMES[name]}
            if name == "AC" and isinstance(entry.get("AC"), dict):
                entry = {"measured": entry.get("measured", True), **entry["AC"]}
            if entry.get("measured") is False:
                item["notMeasured"] = entry.get("reason", "")
            elif name == "IS":
                item["text"] = entry.get("weakest") or ", ".join(entry.get("levels", [])) or "none"
            elif {"point", "low", "high"} <= set(entry):
                item["row"] = _interval(f"{name} · {METRIC_NAMES[name]}", entry)
            elif "point" in entry:
                item["text"] = str(entry["point"])
            items.append(item)
        dimensions.append({"title": title, "question": question, "metrics": items})
    # One reason for every metric (a bare model keeps no record): said once, not seven times.
    reasons = {m.get("notMeasured") for d in dimensions for m in d["metrics"]}
    shared_reason = reasons.pop() if len(reasons) == 1 and None not in reasons else None
    by_category = [
        {**(_interval(name, data.get("taskCompleted")) or {"label": name}),
         "runs": data.get("runs")}
        for name, data in payload.get("byCategory", {}).items()
    ]
    scenario_set = payload.get("scenarioSet", {})
    return {
        "kind": "agent",
        "model": f"Agent · {agent.get('adapter')} · "
                 f"{agent.get('model') or agent.get('endpoint') or 'unnamed'}",
        "adapter": agent.get("adapter"),
        "agent": {k: v for k, v in agent.items()
                  if k not in ("adapter", "params", "protocolHash") and not isinstance(v, dict)},
        "certifiable": payload.get("certifiable"),
        "repeats": payload.get("repeats"),
        "behaviour": rows,
        "behaviourNotMeasured": unmeasured,
        "agentErrors": behaviour.get("agentErrors", 0),
        "instability": payload.get("instability", {}),
        "byCategory": by_category,
        "dimensions": dimensions,
        "auditabilityReason": shared_reason,
        "bias": payload.get("agentBias", {}),
        "toxicity": payload.get("agentToxicity", {}),
        "source": scenario_set.get("source", []),
        "scenarioCount": len(scenario_set.get("scenarios", [])),
        "scenarioSetHash": scenario_set.get("hash"),
        "runs": [_agent_run(r) for r in payload.get("runs", [])],
        "limitations": payload.get("limitations", []),
        "notImplemented": payload.get("notImplemented", []),
    }


def _agent_run(run: dict[str, Any]) -> dict[str, Any]:
    behaviour = run.get("behaviour", {})
    return {"scenario": run.get("scenario"), "repeat": run.get("repeat"),
            "completed": behaviour.get("taskCompleted"),
            "forbidden": behaviour.get("forbiddenAttempted"),
            "resisted": behaviour.get("injectionResisted"),
            "violated": len(behaviour.get("policiesViolated", [])),
            "calls": behaviour.get("calls"),
            "error": behaviour.get("agentError") or (run.get("agent") or {}).get("error")}


def scenario_sources() -> list[dict[str, Any]]:
    """What a new agent run can be pointed at: the reference set and every domain pack."""
    from opaas.smith.scenario import available_packs, load_pack

    sources = [{"value": "reference", "label": "Reference scenarios (21)", "language": "en"}]
    for pack_id in available_packs():
        try:
            pack, loaded = load_pack(pack_id)
        except ValueError:
            continue
        sources.append({"value": f"pack:{pack.id}", "language": pack.language,
                        "label": f"{pack.title} · pack:{pack.id} ({len(loaded)})"})
    return sources


def agent_adapters() -> list[str]:
    from opaas.smith import available_agents

    return list(available_agents())


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
