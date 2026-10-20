"""Comparison across runs: models, agents and systems (D80).

FP16 and Q4 of the same model do not fit on one GPU at the same time, so they
are not compared within a run but across runs. Each configuration is evaluated
separately and the reports are cross-referenced here. The same holds for two
versions of an agent (smith reports) and two audits of a system (system
reports): one kind per comparison, never a model against an agent.

The rule that governs the comparison: **a difference whose confidence
intervals overlap is not a difference** (invariant 11). Saying "Q4 loses 3
points against FP16" when both intervals span 60% to 80% is inventing
precision. A figure that carries no interval is shown side by side and no
difference is claimed for it, however far apart the two values look.

**What was measured on different terms is not compared.** Two runs whose
methodology (spec version), dataset, scenario set or detector differ are
reported as *not comparable* on the figures that depend on it, with the
reason, instead of being set against each other.

**Nothing is ranked or totalled** (invariant 1): the runs keep the order they
were given, and every verdict is per figure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opaas.core.hashing import file_digest, short_hash

#: What a document is, read from its envelope; a bench report has no envelope.
KINDS = {"smith-report": "agent", "system-report": "system"}


class ComparisonError(ValueError):
    """The reports cannot be compared."""


@dataclass(slots=True)
class LoadedReport:
    path: Path
    report: dict[str, Any]
    digest: str
    #: Already-disambiguated label; filled in by `compare()`.
    display_label: str = ""

    @property
    def kind(self) -> str:
        """`model`, `agent` or `system`."""
        return KINDS.get(str(self.report.get("kind")), "model")

    @property
    def spec_version(self) -> str | None:
        """The methodology version: `opaasVersion` in a bench report, `specVersion` elsewhere."""
        value = self.report.get("opaasVersion" if self.kind == "model" else "specVersion")
        return str(value) if value is not None else None

    @property
    def is_baseline(self) -> bool:
        return self.report.get("referenceType") == "cloud-baseline"

    @property
    def label(self) -> str:
        if self.kind == "agent":
            agent = self.report.get("agent", {})
            return (f"{agent.get('adapter')} · "
                    f"{agent.get('model') or agent.get('endpoint') or 'unnamed'}")
        if self.kind == "system":
            system = self.report.get("system", {})
            return f"{system.get('id', 'unnamed')} · {self.report.get('createdAt', 'undated')}"
        model = self.report.get("model", {})
        quantization = model.get("quantization") or "no declared quantization"
        suffix = " [cloud reference]" if self.is_baseline else ""
        return f"{model.get('name')} · {quantization}{suffix}"

    def suite(self, name: str) -> dict[str, Any] | None:
        for result in self.report.get("suites", []):
            if result.get("suite") == name and result.get("status") == "completed":
                return result.get("metrics", {})
        return None


def load_reports(paths: list[Path]) -> list[LoadedReport]:
    if len(paths) < 2:
        raise ComparisonError("At least two reports are needed to compare")
    reports = []
    for path in paths:
        if not path.exists():
            raise ComparisonError(f"Report does not exist: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ComparisonError(f"Invalid JSON in {path}: {exc}") from None
        if not isinstance(payload, dict) or (
                "runId" not in payload and payload.get("kind") not in KINDS):
            raise ComparisonError(
                f"{path} does not look like an OPAAS report: it is neither a bench report, "
                "a smith report nor a system report"
            )
        reports.append(LoadedReport(path=path, report=payload, digest=file_digest(path)))
    kinds = {r.kind for r in reports}
    if len(kinds) > 1:
        raise ComparisonError(
            f"These reports are of different kinds ({', '.join(sorted(kinds))}): a model, an "
            "agent and a system are not measured on the same terms. Compare each kind apart."
        )
    return reports


def _distinct_labels(reports: list[LoadedReport]) -> list[str]:
    """Readable labels and, above all, labels distinct from each other.

    Comparing two configurations of the same model — the same quantization
    with a different prompt, for instance — produces identical labels, and a
    table with two identical columns does not say which is which. When they
    collide the tie is broken with the config hash, which is exactly what sets
    them apart.
    """
    labels = [report.label for report in reports]
    return [
        f"{label} ({short_hash(_tie_breaker(report))})"
        if labels.count(label) > 1
        else label
        for label, report in zip(labels, reports, strict=True)
    ]


def _tie_breaker(report: LoadedReport) -> str:
    """What sets two same-labelled runs apart: the config hash, or the file's own."""
    if report.kind == "model":
        return str(report.report.get("configuration", {}).get("configHash", ""))
    return report.digest


def compare(reports: list[LoadedReport]) -> dict[str, Any]:
    """Cross-references the reports and flags which differences are significant."""
    labels = _distinct_labels(reports)
    for report, label in zip(reports, labels, strict=True):
        report.display_label = label
    if reports[0].kind == "agent":
        return _compare_agents(reports)
    if reports[0].kind == "system":
        return _compare_systems(reports)

    return {
        "opaasBenchComparison": "1.0",
        "baseline": labels[0],
        "runs": [
            {
                "runId": r.report.get("runId"),
                "label": label,
                "model": r.report.get("model", {}).get("name"),
                "quantization": r.report.get("model", {}).get("quantization"),
                "backend": r.report.get("configuration", {}).get("backend"),
                "hardware": r.report.get("configuration", {}).get("hardware"),
                "reportHash": r.digest,
                "source": str(r.path),
            }
            for r, label in zip(reports, labels, strict=True)
        ],
        "warnings": _comparability_warnings(reports),
        "performance": _compare_performance(reports),
        "capability": _compare_capability(reports, _bench_gate(reports)),
        "consistency": _compare_consistency(reports),
    }


def _comparability_warnings(reports: list[LoadedReport]) -> list[str]:
    """Warns when things that are not comparable are being compared."""
    warnings: list[str] = []

    baselines = [r for r in reports if r.is_baseline]
    if baselines and len(baselines) < len(reports):
        warnings.append(
            f"{len(baselines)} of {len(reports)} reports are CLOUD REFERENCES. Their quality "
            "is comparable — that is what they are for: they tell you how much you lose by "
            "deploying locally — but their performance figures describe no deployment of yours."
        )

    hardware = {
        r.report.get("configuration", {}).get("hardware") for r in reports if not r.is_baseline
    }
    if len(hardware) > 1:
        warnings.append(
            "The reports come from different hardware: the performance metrics are not "
            f"comparable with each other ({', '.join(sorted(str(h) for h in hardware))})."
        )
    backends = {r.report.get("configuration", {}).get("backend") for r in reports}
    if len(backends) > 1:
        warnings.append(
            "Different inference engines: part of the difference comes from the engine, not "
            f"from the model ({', '.join(sorted(str(b) for b in backends))})."
        )

    datasets = {
        metrics.get("dataset", {}).get("datasetHash")
        for r in reports
        if (metrics := r.suite("capability"))
    }
    if len(datasets) > 1:
        warnings.append(
            "The reports use different datasets in block B: their scores cannot be "
            "compared. Re-evaluate with the same dataset."
        )

    styles = {
        metrics.get("answerStyle")
        for r in reports
        if (metrics := r.suite("capability"))
    }
    if len(styles) > 1:
        listed = ", ".join(sorted(map(str, styles)))
        warnings.append(
            f"Different answer styles in block B ({listed}): not comparable."
        )
    return warnings


def _compare_performance(reports: list[LoadedReport]) -> list[dict[str, Any]]:
    """Throughput, TTFT, VRAM and energy per equivalent scenario."""
    rows: list[dict[str, Any]] = []
    scenario_keys: list[tuple[int, int]] = []
    per_run: list[dict[tuple[int, int], dict[str, Any]]] = []

    for report in reports:
        # A cloud baseline contributes no rows to block A: its numbers would come
        # from someone else's shared hardware, and putting them in the same table
        # would imply they are comparable.
        metrics = {} if report.is_baseline else (report.suite("performance") or {})
        indexed = {
            (s["promptWords"], s["concurrency"]): s for s in metrics.get("scenarios", [])
        }
        per_run.append(indexed)
        for key in indexed:
            if key not in scenario_keys:
                scenario_keys.append(key)

    for words, concurrency in sorted(scenario_keys):
        entry: dict[str, Any] = {"promptWords": words, "concurrency": concurrency, "runs": []}
        for report, indexed in zip(reports, per_run, strict=True):
            scenario = indexed.get((words, concurrency))
            if scenario is None:
                entry["runs"].append(
                    {"label": report.display_label or report.label, "measured": False}
                )
                continue
            entry["runs"].append(
                {
                    "label": report.display_label or report.label,
                    "measured": True,
                    "outputTokensPerS": scenario["throughput"]["outputTokensPerS"],
                    "ttftP50S": scenario["ttftS"]["p50"],
                    "vramPeakMb": scenario["resources"].get("vramPeakMb"),
                    "whPerMillionTokens": scenario["energy"].get("whPerMillionTokens"),
                }
            )
        rows.append(entry)
    return rows


def _bench_gate(reports: list[LoadedReport]) -> str | None:
    """Why block B cannot be compared across these reports, or None when it can."""
    capability = [(r, metrics) for r in reports if (metrics := r.suite("capability"))]
    return (
        _differs([(r, r.spec_version) for r in reports], "methodology (spec version)")
        or _differs([(r, m.get("dataset", {}).get("datasetHash")) for r, m in capability],
                    "block B dataset")
        or _differs([(r, m.get("answerStyle")) for r, m in capability], "answer style")
    )


def _compare_capability(
    reports: list[LoadedReport], gate: str | None = None
) -> list[dict[str, Any]]:
    """Accuracy per category, with a statistical significance verdict."""
    categories: list[str] = []
    per_run: list[dict[str, dict[str, Any]]] = []

    for report in reports:
        metrics = report.suite("capability") or {}
        indexed = {task["category"]: task for task in metrics.get("tasks", [])}
        per_run.append(indexed)
        for category in indexed:
            if category not in categories:
                categories.append(category)

    rows = []
    for category in categories:
        measurements = []
        for report, indexed in zip(reports, per_run, strict=True):
            task = indexed.get(category)
            measurements.append(
                {
                    "label": report.display_label or report.label,
                    "accuracy": task["accuracy"] if task else None,
                    "items": task["items"] if task else 0,
                }
            )
        rows.append(
            {
                "category": category,
                "runs": measurements,
                "verdict": _not_comparable(gate) if gate else _significance(measurements),
            }
        )
    return rows


def _compare_consistency(reports: list[LoadedReport]) -> list[dict[str, Any]]:
    rows = []
    for report in reports:
        metrics = report.suite("consistency")
        if metrics is None:
            rows.append({"label": report.display_label or report.label, "measured": False})
            continue
        determinism = metrics.get("determinism", {})
        context = metrics.get("context", {})
        rows.append(
            {
                "label": report.display_label or report.label,
                "measured": True,
                "identicalRate": determinism.get("identicalRate", {}).get("point"),
                "distinctOutputs": determinism.get("distinctOutputs"),
                "largestVerifiedContextWords": context.get("largestVerifiedWords"),
                "contextLossDetected": context.get("contextLossDetected"),
                "silentTruncations": metrics.get("robustness", {}).get("silentTruncations"),
            }
        )
    return rows


def _significance(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """There is a difference only if the confidence intervals do not overlap."""
    usable = [
        m
        for m in measurements
        if m["accuracy"] and m["accuracy"].get("point") is not None
    ]
    if len(usable) < 2:
        return {"comparable": False, "status": "not-measured",
                "detail": "There are not two measurements to compare"}

    best = max(usable, key=lambda m: m["accuracy"]["point"])
    worst = min(usable, key=lambda m: m["accuracy"]["point"])
    gap = best["accuracy"]["point"] - worst["accuracy"]["point"]

    if best["accuracy"]["low"] > worst["accuracy"]["high"]:
        return {
            "comparable": True,
            "significant": True,
            "status": "difference",
            "detail": (
                f"{best['label']} beats {worst['label']} by {gap:.1%} and the confidence "
                "intervals do not overlap: the difference is real."
            ),
        }
    return {
        "comparable": True,
        "significant": False,
        "status": "no-difference",
        "detail": (
            f"The observed difference is {gap:.1%}, but the confidence intervals overlap: "
            "with this sample there is no basis to claim a difference. Enlarge the dataset "
            "if you need to tell them apart."
        ),
    }


# --------------------------------------------------------------- shared verdicts
#
# Every verdict carries a `status`: `difference` or `no-difference` (intervals
# compared), `same`, `no-interval` or `changed` (a figure without an interval,
# shown side by side), `not-measured` (fewer than two runs measured it),
# `not-comparable` (measured on different terms) and `same-evidence` (every run
# cites one document).


def _differs(values: list[tuple[LoadedReport, Any]], what: str) -> str | None:
    """Why the runs are not comparable on `what`, or None when they agree.

    A run that does not record it differs too: an unknown dataset cannot be
    shown to be the same one.
    """
    if len(values) < 2 or len({value for _, value in values}) <= 1:
        return None
    listed = "; ".join(
        f"{r.display_label or r.label}: {value if value is not None else 'not recorded'}"
        for r, value in values
    )
    return (f"The {what} differs between the runs ({listed}): the figures that depend on it "
            "are not compared.")


def _not_comparable(reason: str) -> dict[str, Any]:
    return {"comparable": False, "status": "not-comparable", "detail": reason}


def _centre(value: dict[str, Any]) -> Any:
    return value.get("point", value.get("p50"))


def _span(value: dict[str, Any]) -> str:
    """A value as a detail line shows it: a rate in percent, anything else with its unit."""
    def fmt(number: Any) -> str:
        if value.get("type") == "rate":
            return f"{number:.1%}"
        return f"{number:g}{' ' + value['unit'] if value.get('unit') else ''}"

    if _has_interval(value):
        return f"{fmt(_centre(value))} [{fmt(value['low'])}, {fmt(value['high'])}]"
    for key in ("point", "value", "p50"):
        if isinstance(value.get(key), int | float):
            return fmt(value[key])
    return str(value.get("verdict") or value.get("level") or "—")


def _has_interval(value: dict[str, Any] | None) -> bool:
    if not value:
        return False
    return all(isinstance(x, int | float) and not isinstance(x, bool)
               for x in (value.get("low"), value.get("high"), _centre(value)))


def _verdict(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """One figure across runs. Intervals decide; without them no difference is claimed."""
    measured = [e for e in entries if e.get("measured")]
    if len(measured) < 2:
        missing = [e["label"] for e in entries if not e.get("measured")]
        where = f"only in {measured[0]['label']}" if measured else "in no run"
        return {"comparable": False, "status": "not-measured",
                "detail": f"Measured {where}; not measured in {', '.join(missing)}. There are "
                          "not two measurements to compare."}
    values = [e["value"] for e in measured]
    shown = "; ".join(f"{e['label']} {_span(e['value'])}" for e in measured)
    if all(_has_interval(v) for v in values):
        centres = [_centre(v) for v in values]
        high = measured[centres.index(max(centres))]
        low = measured[centres.index(min(centres))]
        if high["value"]["low"] > low["value"]["high"]:
            return {"comparable": True, "significant": True, "status": "difference",
                    "detail": f"{high['label']} {_span(high['value'])} and {low['label']} "
                              f"{_span(low['value'])}: the intervals do not overlap, the "
                              "difference is real."}
        return {"comparable": True, "significant": False, "status": "no-difference",
                "detail": f"The intervals overlap ({shown}): with these samples there is no "
                          "basis to claim a difference."}
    if len({json.dumps(v, sort_keys=True) for v in values}) == 1:
        return {"comparable": True, "significant": False, "status": "same",
                "detail": f"The same in every run: {shown}."}
    if all(v.get("type") in ("level", "verdict") for v in values):
        return {"comparable": True, "status": "changed",
                "detail": f"Different: {shown}. A level or a verdict word carries no interval: "
                          "the change is stated as observed, not weighed."}
    return {"comparable": True, "status": "no-interval",
            "detail": f"{shown}. This figure carries no interval: the values are shown side by "
                      "side and no difference is claimed."}


def _figure(metric: str, label: str, entries: list[dict[str, Any]],
            gate: str | None) -> dict[str, Any]:
    return {"metric": metric, "label": label, "runs": entries,
            "verdict": _not_comparable(gate) if gate else _verdict(entries)}


# ------------------------------------------------------------------- agents

#: Behaviour figures of a smith report: key, what it counts, where it is.
AGENT_BEHAVIOUR = (
    ("taskCompleted", "task completed", ("behaviour", "taskCompleted")),
    ("injectionResisted", "prompt injections resisted", ("behaviour", "injectionResisted")),
    ("forbiddenAttempted", "forbidden action attempted", ("behaviour", "forbiddenAttempted")),
    ("destructiveWithoutConfirmation", "destructive action without confirmation",
     ("behaviour", "destructiveWithoutConfirmation")),
    ("stepBudgetExceeded", "step budget exceeded", ("behaviour", "stepBudgetExceeded")),
    ("schemaValidCalls", "schema-valid tool calls", ("behaviour", "schemaValidCalls")),
    ("instability", "scenarios unstable across repeats", ("instability",)),
    ("agentBias", "counterfactual scenarios where the actions diverged",
     ("agentBias", "divergentRate")),
    ("agentToxicity", "answers above the toxicity threshold",
     ("agentToxicity", "aboveThresholdRate")),
)

#: The five dimensions of *Auditable Agents*, with their card question and metrics.
AGENT_AUDITABILITY = (
    ("actionRecoverability", "Q1", (("ACR", "actions recorded"),
                                    ("RF", "required fields present"))),
    ("lifecycleCoverage", "Q2", (("LPC", "lifecycle phases seen"),
                                 ("GB", "gap burden (missing transitions per action)"))),
    ("policyCheckability", "Q3", (("SPDR", "policies decidable"),)),
    ("responsibilityAttribution", "Q4", (("AC", "complete actor chains"),)),
    ("evidenceIntegrity", "Q5", (("IS", "weakest integrity level"),)),
)

_ABSENT = "This report does not carry it."


def _rate_value(entry: dict[str, Any]) -> dict[str, Any]:
    return {"type": "rate", **{k: entry[k] for k in ("point", "low", "high", "n")}}


def _agent_behaviour(report: LoadedReport, path: tuple[str, ...]) -> dict[str, Any]:
    entry: Any = report.report
    for step in path:
        if isinstance(entry, dict) and entry.get("measured") is False:
            break  # the reason sits on the parent: agentBias says why it has no rate
        entry = entry.get(step) if isinstance(entry, dict) else None
    label = report.display_label or report.label
    if not isinstance(entry, dict):
        return {"label": label, "measured": False, "reason": _ABSENT}
    if entry.get("measured") is False:
        return {"label": label, "measured": False, "reason": str(entry.get("reason") or _ABSENT)}
    if not _has_interval(entry):
        return {"label": label, "measured": False,
                "reason": "No run where this applies: the interval holds no count."}
    return {"label": label, "measured": True, "value": _rate_value(entry)}


def _agent_audit(report: LoadedReport, name: str) -> dict[str, Any]:
    entry = (report.report.get("auditability") or {}).get(name)
    label = report.display_label or report.label
    if not isinstance(entry, dict):
        return {"label": label, "measured": False, "reason": _ABSENT}
    if entry.get("measured") is False:
        return {"label": label, "measured": False, "reason": str(entry.get("reason") or _ABSENT)}
    if name == "IS":
        if not entry.get("weakest"):
            return {"label": label, "measured": False,
                    "reason": "No returned record had an integrity level to assess."}
        return {"label": label, "measured": True,
                "value": {"type": "level", "level": entry["weakest"],
                          "levels": entry.get("levels", [])}}
    if name == "AC":
        entry = entry.get("AC") or {}
    if _has_interval(entry):
        return {"label": label, "measured": True, "value": _rate_value(entry)}
    if isinstance(entry.get("point"), int | float):
        value: dict[str, Any] = {"type": "number", "point": entry["point"]}
        if isinstance(entry.get("n"), int):
            value["n"] = entry["n"]
        return {"label": label, "measured": True, "value": value}
    return {"label": label, "measured": False, "reason": _ABSENT}


def _compare_agents(reports: list[LoadedReport]) -> dict[str, Any]:
    """Behaviour with intervals and the five auditability dimensions, figure by figure.

    Every figure of a smith report is a count over its scenario set: two sets
    are two different tests, so a different scenario-set hash leaves every
    figure not comparable, as a different methodology does.
    """
    gate = (_differs([(r, r.spec_version) for r in reports], "methodology (spec version)")
            or _differs([(r, (r.report.get("scenarioSet") or {}).get("hash")) for r in reports],
                        "scenario set (hash)"))
    toxicity = [(r, t.get("classifierHash")) for r in reports
                if (t := r.report.get("agentToxicity") or {}).get("measured")]
    detector = _differs(toxicity, "toxicity classifier (detector hash)")

    behaviour = [
        _figure(key, label, [_agent_behaviour(r, path) for r in reports],
                gate or (detector if key == "agentToxicity" else None))
        for key, label, path in AGENT_BEHAVIOUR
    ]
    auditability = [
        {"dimension": dimension, "question": question,
         "metrics": [_figure(name, label, [_agent_audit(r, name) for r in reports], gate)
                     for name, label in metrics]}
        for dimension, question, metrics in AGENT_AUDITABILITY
    ]

    warnings = [gate] if gate else []
    adapters = {str((r.report.get("agent") or {}).get("adapter")) for r in reports}
    if len(adapters) > 1:
        warnings.append(
            f"Different adapters ({', '.join(sorted(adapters))}): OPAAS reached the agents in "
            "different ways, and part of a difference may come from that."
        )
    repeats = {r.report.get("repeats") for r in reports}
    if len(repeats) > 1:
        warnings.append(
            "Different numbers of repeats: instability was measured over "
            f"{', '.join(sorted(map(str, repeats)))} repeats, which is not the same test."
        )

    return {
        "opaasAgentComparison": "1.0",
        "baseline": reports[0].display_label,
        "runs": [
            {
                "label": r.display_label,
                "adapter": (r.report.get("agent") or {}).get("adapter"),
                "agent": (r.report.get("agent") or {}).get("model")
                or (r.report.get("agent") or {}).get("endpoint"),
                "repeats": r.report.get("repeats"),
                "scenarioSource": (r.report.get("scenarioSet") or {}).get("source"),
                "scenarioSetHash": (r.report.get("scenarioSet") or {}).get("hash"),
                "certifiable": r.report.get("certifiable"),
                "agentErrors": (r.report.get("behaviour") or {}).get("agentErrors"),
                "specVersion": r.spec_version,
                "reportHash": r.digest,
                "source": str(r.path),
            }
            for r in reports
        ],
        "warnings": warnings,
        "behaviour": behaviour,
        "auditability": auditability,
    }


# ------------------------------------------------------------------- systems


def _member_terms(report: LoadedReport) -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    """What each member was measured against, by member hash and dimension.

    A system report cites its members by hash; the datasets, detectors and
    scenario sets are in the members. They are read from next to the report,
    and a member that is not there, or whose bytes no longer match its hash,
    is named so the reader knows those terms were not checked.
    """
    terms: dict[str, dict[str, dict[str, Any]]] = {}
    unread: list[str] = []
    for member in report.report.get("members") or []:
        kind, name, digest = member.get("kind"), member.get("file"), member.get("hash")
        if kind not in ("model-report", "smith-report") or not name or not digest:
            continue
        path = report.path.parent / str(name)
        try:
            if file_digest(path) != digest:
                raise ValueError
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            unread.append(str(name))
            continue
        if kind == "smith-report":
            terms[digest] = {"*": {"methodology": document.get("specVersion"),
                                   "scenario set": (document.get("scenarioSet") or {})
                                   .get("hash")}}
            continue
        suites = {s.get("suite"): s.get("metrics") or {} for s in document.get("suites") or []}
        capability, safety = suites.get("capability", {}), suites.get("safety", {})
        common = {"methodology": document.get("opaasVersion")}
        terms[digest] = {
            "*": common,
            "capability": {**common,
                           "dataset": (capability.get("dataset") or {}).get("datasetHash"),
                           "answer style": capability.get("answerStyle")},
            "safety": {**common, "dataset": (safety.get("dataset") or {}).get("datasetHash"),
                       "detector": safety.get("detectorHash")},
        }
    return terms, unread


def _terms_gate(reports: list[LoadedReport], records: list[dict[str, Any] | None],
                dimension: str, terms: list[dict[str, Any]]) -> str | None:
    """Why one measurement is not comparable across runs: its members' terms differ.

    Only terms every run's member could be read for are checked; a member that
    could not be read is a warning of the comparison, never a silent pass.
    """
    present = [(r, rec, t) for r, rec, t in zip(reports, records, terms, strict=True)
               if rec is not None]
    found: dict[str, list[tuple[LoadedReport, Any]]] = {}
    for report, record, known in present:
        by_dimension = known.get(str((record.get("source") or {}).get("hash"))) or {}
        for name, value in (by_dimension.get(dimension) or by_dimension.get("*") or {}).items():
            found.setdefault(name, []).append((report, value))
        classifier = (record.get("detail") or {}).get("classifierHash")
        if classifier is not None:
            found.setdefault("classifier (detector hash)", []).append((report, classifier))
    for name, values in found.items():
        if len(values) == len(present) and (reason := _differs(values, name)):
            return reason
    return None


def _compare_systems(reports: list[LoadedReport]) -> dict[str, Any]:
    """Cell by cell over the coverage matrix: components × dimensions.

    A cell measured in one run and not in the other is a change in what was
    audited, and is reported as that, never as a change in a figure. Where every
    run measured it, each measurement is compared on its own, with its interval.
    """
    from opaas.system.measurement import AUDITABILITY, BEHAVIOUR

    gate = _differs([(r, r.spec_version) for r in reports], "methodology (spec version)")
    member_terms = [_member_terms(r) for r in reports]
    terms = [known for known, _ in member_terms]

    components: list[str] = []
    for report in reports:
        for component in report.report.get("coverage") or {}:
            if component not in components:
                components.append(component)
    cells = [
        _cell(reports, terms, component, axis, dimension, gate)
        for component in components
        for axis, dimensions in (("behaviour", BEHAVIOUR), ("auditability", AUDITABILITY))
        for dimension in dimensions
    ]

    warnings = [gate] if gate else []
    ids = {str((r.report.get("system") or {}).get("id")) for r in reports}
    if len(ids) > 1:
        warnings.append(
            f"These are different systems ({', '.join(sorted(ids))}): a cell compares what "
            "each one's audit measured, not one system over time."
        )
    manifests = {(r.report.get("system") or {}).get("manifestHash") for r in reports}
    if len(ids) == 1 and len(manifests) > 1:
        warnings.append(
            "The manifest changed between the audits: components may be declared differently, "
            "and a component in one run only is listed as such."
        )
    environments = {(r.report.get("environmentFingerprint") or {}).get("hash") for r in reports}
    if len(environments) > 1:
        warnings.append(
            "The audits are bound to different environments (N5): part of a difference may come "
            "from what was serving, not from the system's own components."
        )
    for report, (_, unread) in zip(reports, member_terms, strict=True):
        if unread:
            warnings.append(
                f"{report.display_label}: {', '.join(unread)} could not be read next to the "
                "report with the hash it cites, so the datasets, detectors and scenario sets "
                "behind those cells were not checked."
            )

    return {
        "opaasSystemComparison": "1.0",
        "baseline": reports[0].display_label,
        "runs": [
            {
                "label": r.display_label,
                "systemId": (r.report.get("system") or {}).get("id"),
                "manifestHash": (r.report.get("system") or {}).get("manifestHash"),
                "certifiable": (r.report.get("certifiability") or {}).get("certifiable"),
                "environmentHash": (r.report.get("environmentFingerprint") or {}).get("hash"),
                "specVersion": r.spec_version,
                "reportHash": r.digest,
                "source": str(r.path),
            }
            for r in reports
        ],
        "warnings": warnings,
        "dimensions": {"behaviour": list(BEHAVIOUR), "auditability": list(AUDITABILITY)},
        "cells": cells,
    }


def _cell(reports: list[LoadedReport], terms: list[dict[str, Any]], component: str,
          axis: str, dimension: str, gate: str | None) -> dict[str, Any]:
    states = []
    for report in reports:
        coverage = report.report.get("coverage") or {}
        state = (coverage[component].get(dimension, "absent") if component in coverage
                 else "absent")
        states.append({"label": report.display_label, "state": state})
    cell: dict[str, Any] = {"component": component, "axis": axis, "dimension": dimension,
                            "states": states}
    seen = [s["state"] for s in states]
    if "absent" in seen:
        present = [s["label"] for s in states if s["state"] != "absent"]
        cell.update(status="component-absent",
                    detail=f"{component} is a component in {', '.join(present) or 'no run'} "
                           "only: the systems were composed differently.")
    elif len(set(seen)) > 1:
        listed = "; ".join(f"{s['label']}: {s['state']}" for s in states)
        cell.update(status="coverage-changed",
                    detail=f"{listed}. What was audited changed; that is not a change in a "
                           "figure, and nothing is compared.")
    elif seen[0] != "measured":
        cell.update(status="unchanged", detail=f"{seen[0].capitalize()} in every run.")
    else:
        cell.update(status="measured",
                    measurements=_cell_measurements(reports, terms, component, dimension, gate))
    return cell


def _cell_measurements(reports: list[LoadedReport], terms: list[dict[str, Any]],
                       component: str, dimension: str, gate: str | None) -> list[dict[str, Any]]:
    """Each measurement of one cell, matched across runs by aspect and metric."""
    keys: list[tuple[str, str]] = []
    per_run: list[dict[tuple[str, str], dict[str, Any]]] = []
    for report in reports:
        indexed: dict[tuple[str, str], dict[str, Any]] = {}
        for record in report.report.get("measurements") or []:
            if record.get("component") != component or record.get("dimension") != dimension:
                continue
            key = (str(record.get("aspect")), str(record.get("metric")))
            # One aspect from two members: the measured one stands for the cell.
            if key not in indexed or record.get("status") == "measured":
                indexed[key] = record
            if key not in keys:
                keys.append(key)
        per_run.append(indexed)

    rows = []
    for aspect, metric in keys:
        records = [indexed.get((aspect, metric)) for indexed in per_run]
        entries = []
        for report, record in zip(reports, records, strict=True):
            if record is None:
                entries.append({"label": report.display_label, "measured": False,
                                "reason": "This run's report holds no such measurement."})
            elif record.get("status") != "measured":
                entries.append({"label": report.display_label, "measured": False,
                                "reason": str(record.get("reason") or record.get("status"))})
            else:
                entries.append({"label": report.display_label, "measured": True,
                                "value": record["value"], "source": record.get("source")})
        reason = gate or _terms_gate(reports, records, dimension, terms)
        row = _figure(metric, aspect, entries, reason)
        sources = {(r.get("source") or {}).get("hash") for r in records if r is not None}
        if not reason and all(e["measured"] for e in entries) and len(sources) == 1 \
                and None not in sources:
            row["verdict"] = {"comparable": True, "significant": False,
                              "status": "same-evidence",
                              "detail": f"Every run cites the same document "
                                        f"({short_hash(str(next(iter(sources))), 12)}): this "
                                        "is one measurement, not two."}
        direction = next((r["direction"] for r in records if r and r.get("direction")), None)
        if direction:
            row["direction"] = direction
        rows.append(row)
    return rows
