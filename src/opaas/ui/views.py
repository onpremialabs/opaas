"""What the dashboard's pages show, built from files and nothing else (D79).

Every function here reads a run directory or the mascot grid and returns plain
data for a template. None of them measures, scores or ranks: a figure is
shown per category with its interval, in the order the report gives it, and
what was not measured is shown as not measured, with its reason (invariants 1
and 2).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opaas.core.store import RUN_FILE, StoreError, list_runs, load_run, read_progress, verify_files

#: Where a run keeps the report the pages summarise, in order of preference.
REPORTS = ("report.json", "system-audit/system-report.json", "comparison.json")

#: A discovered engine's `kind` -> the `--backend` value `opaas run` accepts.
ENGINE_KIND_TO_BACKEND = {"ollama": "ollama", "vllm": "vllm", "openai-compatible": "openai"}

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
        record.update(_describe(store / record["runId"], record))
    return list(reversed(records))


def _option(words: list[str], *names: str) -> str | None:
    for index, word in enumerate(words):
        if word in names and index + 1 < len(words):
            return words[index + 1]
    return None


def _describe(directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    """What the history filters on: model, backend, quantization, deployment fingerprint.

    Taken from the command and, for a model run, from its report; a run that
    does not say is left without, never guessed.
    """
    command = record.get("command", [])
    found: dict[str, Any] = {"model": _option(command, "--model", "-m"),
                             "backend": _option(command, "--backend", "-b")}
    if record.get("kind") in ("smith", "audit") and record.get("status") in (
            "completed", "partial"):
        name = report_name(directory, record)
        if name and name != "comparison.json":
            found.update(reportPath=str(directory / name),
                         compareKind="agent" if record["kind"] == "smith" else "system")
    if record.get("kind") == "bench" and "report.json" in record.get("files", {}):
        found["reportPath"] = str(directory / "report.json")
        found["compareKind"] = "model"
        try:
            report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        model = report.get("model") if isinstance(report.get("model"), dict) else {}
        configuration = report.get("configuration", {})
        found.update({
            "model": model.get("name") or found["model"],
            "quantization": model.get("quantization"),
            "backend": configuration.get("backend") or found["backend"],
            "fingerprint": (report.get("deploymentFingerprint") or {}).get("hash"),
            "referenceType": report.get("referenceType"),
        })
    return {k: v for k, v in found.items() if v is not None}


#: The history's filters, and how each one matches a run.
FILTERS = ("kind", "status", "model", "backend", "version", "fingerprint", "since", "until")


def filter_runs(records: list[dict[str, Any]], query: dict[str, str]) -> list[dict[str, Any]]:
    """Runs matching every filter given. Text filters match a part, case aside."""
    def keep(record: dict[str, Any]) -> bool:
        for name in FILTERS:
            wanted = (query.get(name) or "").strip()
            if not wanted:
                continue
            if name in ("kind", "status") and record.get(name) != wanted:
                return False
            if name in ("model", "backend") and wanted.lower() not in str(
                    record.get(name, "")).lower():
                return False
            if name == "version" and record.get("opaasVersion") != wanted:
                return False
            if name == "fingerprint" and not str(record.get("fingerprint", "")).startswith(
                    wanted if wanted.startswith("sha256:") else f"sha256:{wanted}"):
                return False
            started = str(record.get("startedAt", ""))[:10]
            if name == "since" and started < wanted:
                return False
            if name == "until" and started > wanted:
                return False
        return True

    return [r for r in records if keep(r)]


def latest_discover(store: Path, *, max_age_s: float = 300.0) -> dict[str, Any] | None:
    """The most recent usable `discover scan` run, if still within `max_age_s`.

    `discover gaps` shares the same `kind`: the command itself, not only the
    kind, decides which runs count. None if every scan is stale or there is
    none, so `/new` knows to launch a fresh one.
    """
    now = datetime.now(timezone.utc)
    for record in runs(store):  # newest first
        if record.get("kind") != "discover" or (record.get("command") or [])[2:3] != ["scan"]:
            continue
        if record.get("status") not in ("completed", "partial"):
            continue
        try:
            started = datetime.fromisoformat(str(record["startedAt"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        age = (now - started).total_seconds()
        if age <= max_age_s:
            return {"runId": record["runId"], "ageSeconds": round(age)}
        return None  # newest first: an older one would be staler still
    return None


def discover_picker(store: Path, run_id: str) -> dict[str, Any] | None:
    """A discover run's inventory, laid out for `/new`'s picker.

    None if the run does not exist or wrote no inventory (it failed before
    writing anything): the page then falls back to the manual form.
    """
    from opaas.ui.jobs import REROUTED_AGENTS

    directory = store / run_id
    try:
        record = load_run(directory)
    except (StoreError, ValueError):
        return None
    if "inventory.json" not in record.get("files", {}):
        return None
    try:
        payload = json.loads((directory / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    inventory = payload.get("inventory") or {}
    smith_adapters = set(agent_adapters())
    engines = []
    for engine in inventory.get("engines", []):
        models = [{"name": m.get("name"), "digest": m.get("digest"),
                  "quantization": m.get("quantization"), "parameterSize": m.get("parameterSize")}
                  for m in engine.get("models", [])]
        engines.append({
            "kind": engine.get("kind"), "port": engine.get("port"),
            "version": engine.get("version"), "bind": engine.get("bind"),
            "backend": ENGINE_KIND_TO_BACKEND.get(engine.get("kind")),
            "baseUrl": f"http://127.0.0.1:{engine['port']}" if engine.get("port") else None,
            "models": models,
        })
    stack = inventory.get("agentStack") or {}
    agents = []
    for entry in stack.get("agentEndpoints", []):
        adapter = entry.get("adapter") or entry.get("kind")
        agents.append({
            "kind": entry.get("kind"), "adapter": adapter, "name": entry.get("name"),
            "endpoint": f"http://{entry['endpoint']}" if entry.get("endpoint") else None,
            "basis": entry.get("basis"),
            "runnable": adapter in smith_adapters,
            "rerouted": adapter in REROUTED_AGENTS,
        })
    return {
        "runId": run_id, "hash": inventory.get("hash"), "engines": engines, "agents": agents,
        "unidentified": inventory.get("unidentified", []),
        "notObserved": stack.get("notObserved", []),
    }


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
    if "opaasBenchComparison" in payload:
        return _comparison(payload)
    if "opaasAgentComparison" in payload:
        return _agent_comparison(payload)
    if "opaasSystemComparison" in payload:
        return _system_comparison(payload)
    if payload.get("kind") == "system-report":
        return _system(payload)
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


# --- Comparisons (opaas compare, bench/compare.py) --------------------------

def _comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """`opaas compare`'s own result, laid out: nothing is recomputed or re-judged here.

    Each category keeps the CLI's verdict, word for word; the intervals are the
    reports'. The order of runs is the order they were given, never a ranking.
    """
    runs = [{**run, "storeRun": _store_run(run.get("source"))} for run in payload.get("runs", [])]
    # Two runs of one configuration get the same label from the CLI; on screen
    # each bar must still say which run it is, so the run id leads it then.
    labels = [r.get("label") for r in runs]
    shown = {r.get("label"): r.get("label") for r in runs}
    if len(set(labels)) < len(labels):
        shown = {}
    def display(label: str, index: int) -> str:
        if label in shown:
            return label
        run = runs[index] if index < len(runs) else {}
        return f"{run.get('storeRun') or run.get('runId')} · {label}"
    capability = []
    for row in payload.get("capability", []):
        bars = []
        for index, m in enumerate(row.get("runs", [])):
            label = display(m["label"], index)
            if m.get("accuracy") and m["accuracy"].get("point") is not None:
                bars.append(_interval(label, {**m["accuracy"], "n": m.get("items")}))
            else:
                bars.append({"label": label, "missing": True})
        capability.append({"category": row.get("category"), "bars": bars,
                           "verdict": row.get("verdict", {})})
    return {
        "kind": "comparison",
        "model": f"Comparison · {len(runs)} runs",
        "runs": runs,
        "warnings": payload.get("warnings", []),
        "capability": capability,
        "performance": payload.get("performance", []),
        "consistency": payload.get("consistency", []),
    }


#: A run id as the store allocates them (core/store.py).
_RUN_ID = re.compile(r"^\d{8}-\d{3}$")


def _store_run(source: Any) -> str | None:
    """The store run a compared report lives in, read from its path; None if outside one."""
    path = Path(str(source or ""))
    for parent in (path.parent, path.parent.parent):
        if _RUN_ID.match(parent.name):
            return parent.name
    return None


#: The CLI's verdict status, as the page words it and the class it is drawn with.
#: Words only: the status itself is `opaas compare`'s.
VERDICT_WORDS = {
    "difference": ("A difference", "v-real"),
    "no-difference": ("No difference", "v-none"),
    "same": ("The same", "v-none"),
    "same-evidence": ("Same evidence", "v-none"),
    "no-interval": ("No difference claimed", "v-none"),
    "changed": ("Changed", "v-real"),
    "not-measured": ("Cannot compare", "v-cannot"),
    "not-comparable": ("Not comparable", "v-cannot"),
}


def _figure(figure: dict[str, Any], label: str | None = None) -> dict[str, Any]:
    """One figure of a comparison: a bar per run where the CLI gave an interval, else text."""
    bars = []
    for entry in figure.get("runs", []):
        value = entry.get("value") or {}
        if not entry.get("measured"):
            bars.append({"label": entry.get("label"), "missing": True,
                         "why": entry.get("reason", "")})
        elif value.get("type") == "rate":
            bars.append(_interval(entry.get("label"), value))
        else:
            bars.append({"label": entry.get("label"), "text": _value_text(value)})
    verdict = figure.get("verdict", {})
    word, css = VERDICT_WORDS.get(verdict.get("status"), (verdict.get("status"), "v-cannot"))
    return {"label": label or figure.get("label"), "metric": figure.get("metric"), "bars": bars,
            "verdict": verdict, "word": word, "css": css}


def _value_text(value: dict[str, Any]) -> str:
    """A value without an interval bar, as the report gives it, with its unit."""
    if value.get("type") in ("level", "verdict"):
        return str(value.get("level") or value.get("verdict"))
    centre = next((value[k] for k in ("point", "p50", "value") if k in value), None)
    text = f"{centre}{' ' + value['unit'] if value.get('unit') else ''}"
    if value.get("low") is not None and value.get("high") is not None:
        text += f" [{value['low']}, {value['high']}]"
    return text


def _agent_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """`opaas compare` over smith reports, laid out: behaviour, then the five dimensions."""
    names = {"actionRecoverability": "Action recoverability",
             "lifecycleCoverage": "Lifecycle coverage", "policyCheckability": "Policy checkability",
             "responsibilityAttribution": "Responsibility attribution",
             "evidenceIntegrity": "Evidence integrity"}
    runs = [{**run, "storeRun": _store_run(run.get("source"))} for run in payload.get("runs", [])]
    return {
        "kind": "agent-comparison",
        "model": f"Agent comparison · {len(runs)} runs",
        "runs": runs,
        "warnings": payload.get("warnings", []),
        "behaviour": [_figure(f) for f in payload.get("behaviour", [])],
        "dimensions": [
            {"question": d.get("question"),
             "title": names.get(d.get("dimension"), d.get("dimension")),
             "metrics": [_figure(m, f"{m.get('metric')} · {m.get('label')}")
                         for m in d.get("metrics", [])]}
            for d in payload.get("auditability", [])],
    }


def _system_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """`opaas compare` over system reports, laid out cell by cell, as the CLI wrote them."""
    runs = [{**run, "storeRun": _store_run(run.get("source"))} for run in payload.get("runs", [])]
    cells = payload.get("cells", [])
    # A component in one audit only is absent from every one of its cells: said once.
    changed, absent = [], set()
    for cell in cells:
        if cell.get("status") == "coverage-changed":
            changed.append(cell)
        elif cell.get("status") == "component-absent" and cell.get("component") not in absent:
            absent.add(cell.get("component"))
            changed.append({**cell, "dimension": "every dimension"})
    measured = [
        {"component": c.get("component"),
         "dimension": AXIS_LABELS.get(c.get("dimension"), c.get("dimension")),
         "figures": [_figure(m) for m in c.get("measurements", [])]}
        for c in cells if c.get("status") == "measured"]
    components = []
    for cell in cells:
        if cell.get("component") not in components:
            components.append(cell.get("component"))
    order = [*payload.get("dimensions", {}).get("behaviour", []),
             *payload.get("dimensions", {}).get("auditability", [])]
    by_cell = {(c.get("component"), c.get("dimension")): c for c in cells}
    matrix = [{"component": component,
               "cells": [{"status": (cell := by_cell.get((component, d), {})).get("status"),
                          "states": " · ".join(f"{s.get('label')}: {s.get('state')}"
                                               for s in cell.get("states", []))}
                         for d in order]}
              for component in components]
    return {
        "kind": "system-comparison",
        "model": f"System comparison · {len(runs)} runs",
        "runs": runs,
        "warnings": payload.get("warnings", []),
        "axis": [AXIS_LABELS.get(d, d) for d in order],
        "matrix": matrix,
        "changed": changed,
        "measured": measured,
    }


# --- Systems (system reports, D60) ------------------------------------------

#: The two axes of a system report, fixed for every system (system-audit.md §3).
BEHAVIOUR_AXIS = ("performance", "capability", "consistency", "safety", "compliance")
AUDITABILITY_AXIS = ("actionRecoverability", "lifecycleCoverage", "policyCheckability",
                     "responsibilityAttribution", "evidenceIntegrity", "degradation")
AXIS_LABELS = {
    "performance": "A perf.", "capability": "B capab.", "consistency": "C consist.",
    "safety": "D safety", "compliance": "E compl.", "actionRecoverability": "Q1 actions",
    "lifecycleCoverage": "Q2 lifecycle", "policyCheckability": "Q3 policies",
    "responsibilityAttribution": "Q4 attribution", "evidenceIntegrity": "Q5 integrity",
    "degradation": "Q6 degradation",
}


def _system(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = payload.get("coverage", {})
    reasons: dict[tuple[str, str], list[str]] = {}
    for m in payload.get("measurements", []):
        if m.get("status") != "measured" and m.get("reason"):
            key = (str(m.get("component")), str(m.get("dimension")))
            if m["reason"] not in reasons.setdefault(key, []):
                reasons[key].append(m["reason"])
    declared = [c.get("id") for c in payload.get("composition", {}).get("components", [])]
    order = [c for c in declared if c in coverage] + [c for c in coverage if c not in declared]
    rows = [{"component": component,
             "cells": [{"dimension": d, "state": coverage[component].get(d, "not-applicable"),
                        "why": " · ".join(reasons.get((component, d), []))}
                       for d in (*BEHAVIOUR_AXIS, *AUDITABILITY_AXIS)]}
            for component in order]
    names = {"actionRecoverability": "Action recoverability",
             "lifecycleCoverage": "Lifecycle coverage", "policyCheckability": "Policy checkability",
             "responsibilityAttribution": "Responsibility attribution",
             "evidenceIntegrity": "Evidence integrity", "degradation": "Degradation"}
    card = [{"question": q, "dimension": names.get(entry.get("dimension"), entry.get("dimension")),
             "answers": len(entry.get("answers", [])), "gaps": entry.get("gaps", []),
             "notApplicable": entry.get("notApplicable", [])}
            for q, entry in payload.get("card", {}).items()]
    system = payload.get("system", {})
    certifiability = payload.get("certifiability", {})
    return {
        "kind": "system",
        "model": f"System · {system.get('id', 'unnamed')}",
        "purpose": system.get("purpose"),
        "certifiable": certifiability.get("certifiable"),
        "reasons": certifiability.get("reasons", []),
        "behaviourAxis": [AXIS_LABELS[d] for d in BEHAVIOUR_AXIS],
        "auditabilityAxis": [AXIS_LABELS[d] for d in AUDITABILITY_AXIS],
        "rows": rows,
        "card": card,
        "composition": payload.get("composition", {}),
        "steps": payload.get("steps", []),
        "members": payload.get("members", []),
        "thresholds": payload.get("thresholds"),
        "limitations": payload.get("limitations", []),
        "environment": payload.get("environmentFingerprint", {}),
    }


def stored_reports(store: Path) -> list[dict[str, Any]]:
    """Runs whose report a system audit can cite with --member, newest first."""
    found = []
    for record in runs(store):
        if record.get("kind") not in ("bench", "smith") or record.get("status") not in (
                "completed", "partial"):
            continue
        name = report_name(store / record["runId"], record)
        if name:
            found.append({"runId": record["runId"], "kind": record["kind"],
                          "path": str(store / record["runId"] / name),
                          "command": " ".join(record.get("command", [])[1:])[:70]})
    return found


# --- Sentinel (read where the operator says, D46/D50) -----------------------

def read_jsonl(path: Path, *, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Complete JSON lines of a growing file from `offset`, and where to read next."""
    if not path.is_file():
        return [], offset
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    complete = data[: data.rfind(b"\n") + 1]
    lines = []
    for raw in complete.splitlines():
        try:
            lines.append(json.loads(raw.decode("utf-8")))
        except ValueError:
            continue
    return lines, offset + len(complete)


def sentinel_event(line: dict[str, Any]) -> dict[str, Any]:
    """An AuditEvent as the page shows it: what, who, decided how. Digests stay digests (N4).

    A log line wraps the event in its chain link (`seq`, `previousHash`, `event`);
    a bare event, as other importers write them, is read as it is.
    """
    record = line.get("event") if isinstance(line.get("event"), dict) else line
    chain = record.get("actorChain") or []
    return {
        "seq": line.get("seq"),
        "ts": record.get("ts"), "action": record.get("action"), "target": record.get("target"),
        "phase": record.get("phase"),
        "actors": " > ".join(f"{a.get('role')}:{a.get('id')}" for a in chain
                             if isinstance(a, dict)),
        "verdicts": [{"policy": d.get("policy"), "verdict": d.get("verdict")}
                     for d in record.get("policyDecisions", []) if isinstance(d, dict)],
        "pending": (record.get("extra") or {}).get("pendingId"),
        "durationMs": record.get("durationMs"), "error": record.get("error"),
        "digest": (record.get("args") or {}).get("digest") if isinstance(record.get("args"),
                                                                           dict) else None,
    }


def pending_actions(queue: Path) -> list[dict[str, Any]]:
    """Actions the gate holds for a human, as the queue keeps them."""
    if not queue.is_dir():
        return []
    from opaas.sentinel.gate import ApprovalQueue

    entries = []
    for entry in ApprovalQueue(queue).pending():
        chain = entry.get("actorChain") or []
        entries.append({
            "id": entry.get("id"), "submittedAt": entry.get("submittedAt"),
            "target": entry.get("target"),
            "actors": " > ".join(f"{a.get('role')}:{a.get('id')}" for a in chain
                                 if isinstance(a, dict)),
            "policies": [p.get("policy") for p in entry.get("policies", [])
                         if isinstance(p, dict)],
            "digest": (entry.get("args") or {}).get("digest")
            if isinstance(entry.get("args"), dict) else None,
        })
    return entries


def run_for_job(store: Path, job_id: str) -> dict[str, Any] | None:
    """The run a dashboard job produced: among every run sharing its id, the one
    that started first — the command the job line actually launched.

    A nested command (`opaas audit run`'s own steps, D60) opens its own run in
    the same process, inheriting the same job id from the environment. The
    first one to open is always the launched command itself, never a step it
    ran along the way.
    """
    matches = _runs_for_job(store, job_id)
    return matches[0] if matches else None


def _runs_for_job(store: Path, job_id: str) -> list[dict[str, Any]]:
    """Every run sharing a job id, oldest first."""
    if not store.is_dir():
        return []
    found = []
    for directory in sorted(store.iterdir()):
        path = directory / RUN_FILE
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if record.get("jobId") == job_id:
            found.append(load_run(directory))
    return found


def subruns_for_job(store: Path, job_id: str, primary_run_id: str) -> list[dict[str, Any]]:
    """A job's runs other than its primary one, oldest first: an audit's own steps."""
    return [r for r in _runs_for_job(store, job_id) if r["runId"] != primary_run_id]


#: `cli/audit.py`'s `invoke()` logs the subcommand name as `phase`; `run` is
#: bench's own verb, not a step's name.
STEP_ARG_TO_NAME = {"discover": "discover", "run": "bench", "smith": "smith",
                    "sentinel": "sentinel", "inquest": "inquest", "swarm": "swarm"}


def audit_live_steps(store: Path, record: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The 6 steps of a running `opaas audit run`, each with its live sub-run(s).

    Not the final truth: once the audit closes, its system report's own
    `steps` (already shown on the run page) says what happened, including
    what was skipped. This is only what to show while it runs. Each
    step-start line the audit's own progress log carries (`cli/audit.py`)
    pairs, in order, with the nested run it caused — a skipped step never
    logs a line and never opens a run, so the pairing never drifts.
    """
    from opaas.system.orchestrate import STEPS

    if record.get("kind") != "audit" or not record.get("jobId"):
        return None
    progress, _ = read_progress(store / record["runId"])
    started = [line for line in progress if line.get("phase")]
    subruns = subruns_for_job(store, record["jobId"], record["runId"])
    steps: dict[str, list[dict[str, Any]]] = {name: [] for name in STEPS}
    for line, sub in zip(started, subruns, strict=False):
        name = STEP_ARG_TO_NAME.get(line["phase"])
        if name is not None:
            steps[name].append(_subrun_summary(store, sub))
    return [{"name": name, "invocations": steps[name]} for name in STEPS]


def _subrun_summary(store: Path, sub: dict[str, Any]) -> dict[str, Any]:
    """One nested run as the stepper shows it: its status, and its own percent if it has one."""
    label = _option(sub.get("command") or [], "--model", "-m") or sub.get("endpoint")
    percent = None
    if sub.get("status") == "running":
        lines, _ = read_progress(store / sub["runId"])
        percent = next((line.get("percent") for line in reversed(lines)
                        if line.get("percent") is not None), None)
    return {"runId": sub["runId"], "status": sub.get("status"), "label": label, "percent": percent}


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
