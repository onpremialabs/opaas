import json

import pytest

from opaas.bench.compare import ComparisonError, compare, load_reports
from opaas.core.hashing import file_digest


def _report(tmp_path, name, *, quantization, accuracy, hardware="1x RTX 4090 24GB", **overrides):
    """Minimal report with what the comparator looks at."""
    payload = {
        "runId": f"2026-{name}",
        "status": "evaluated",
        "model": {"name": "Vega-7B", "quantization": quantization},
        "configuration": {"backend": "vLLM 0.9.1", "hardware": hardware},
        "suites": [
            {
                "suite": "performance",
                "status": "completed",
                "metrics": {
                    "scenarios": [
                        {
                            "promptWords": 128,
                            "concurrency": 1,
                            "throughput": {"outputTokensPerS": overrides.get("tps", 50.0)},
                            "ttftS": {"p50": 0.1},
                            "resources": {"vramPeakMb": overrides.get("vram", 14000)},
                            "energy": {"whPerMillionTokens": 400.0},
                        }
                    ]
                },
            },
            {
                "suite": "capability",
                "status": "completed",
                "metrics": {
                    "answerStyle": overrides.get("style", "cot"),
                    "dataset": {"datasetHash": overrides.get("dataset_hash", "sha256:abc")},
                    "tasks": [
                        {
                            "category": "reasoning",
                            "items": accuracy[2],
                            "accuracy": {
                                "point": accuracy[0],
                                "low": accuracy[1],
                                "high": accuracy[2] if len(accuracy) < 4 else accuracy[3],
                                "n": 200,
                            },
                        }
                    ],
                },
            },
        ],
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_at_least_two_reports_are_required(tmp_path):
    path = _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.75, 200, 0.85))
    with pytest.raises(ComparisonError, match="At least two"):
        load_reports([path])


def test_a_file_that_is_not_a_report_is_rejected(tmp_path):
    bad = tmp_path / "whatever.json"
    bad.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    good = _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.75, 200, 0.85))
    with pytest.raises(ComparisonError, match="does not look like an OPAAS report"):
        load_reports([good, bad])


def test_identical_configurations_get_distinguishable_labels(tmp_path):
    """Comparing two configurations of the same model used to give two identical columns."""
    paths = [
        _report(tmp_path, "a", quantization="Q4", accuracy=(0.52, 0.44, 200, 0.60)),
        _report(tmp_path, "b", quantization="Q4", accuracy=(0.69, 0.62, 200, 0.76)),
    ]
    # Same model and quantization, different configuration.
    for path, config_hash in zip(paths, ("sha256:aaaa1111", "sha256:bbbb2222"), strict=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["configuration"]["configHash"] = config_hash
        path.write_text(json.dumps(payload), encoding="utf-8")

    labels = [run["label"] for run in compare(load_reports(paths))["runs"]]
    assert labels[0] != labels[1]
    assert "aaaa1111" in labels[0] and "bbbb2222" in labels[1]


def test_distinct_configurations_keep_the_readable_label(tmp_path):
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9)),
        _report(tmp_path, "b", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9)),
    ]
    labels = [run["label"] for run in compare(load_reports(paths))["runs"]]
    assert labels == ["Vega-7B · FP16", "Vega-7B · Q4"]


def test_runs_are_labelled_by_quantization(tmp_path):
    paths = [
        _report(tmp_path, "fp16", quantization="FP16", accuracy=(0.80, 0.74, 200, 0.86)),
        _report(tmp_path, "q4", quantization="GGUF Q4_K_M", accuracy=(0.78, 0.72, 200, 0.84)),
    ]
    comparison = compare(load_reports(paths))
    assert [run["quantization"] for run in comparison["runs"]] == ["FP16", "GGUF Q4_K_M"]
    assert all(run["reportHash"].startswith("sha256:") for run in comparison["runs"])


def test_overlapping_intervals_are_not_called_a_difference(tmp_path):
    """The comparator's core: 2 points with overlapping intervals is not a difference."""
    paths = [
        _report(tmp_path, "fp16", quantization="FP16", accuracy=(0.80, 0.74, 200, 0.86)),
        _report(tmp_path, "q4", quantization="Q4", accuracy=(0.78, 0.72, 200, 0.84)),
    ]
    verdict = compare(load_reports(paths))["capability"][0]["verdict"]
    assert verdict["comparable"] is True
    assert verdict["significant"] is False
    assert "intervals overlap" in verdict["detail"]


def test_separated_intervals_are_called_a_real_difference(tmp_path):
    paths = [
        _report(tmp_path, "fp16", quantization="FP16", accuracy=(0.90, 0.86, 200, 0.94)),
        _report(tmp_path, "q2", quantization="Q2", accuracy=(0.50, 0.44, 200, 0.56)),
    ]
    verdict = compare(load_reports(paths))["capability"][0]["verdict"]
    assert verdict["significant"] is True
    assert "do not overlap" in verdict["detail"]


def test_performance_rows_line_up_by_scenario(tmp_path):
    paths = [
        _report(tmp_path, "fp16", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9), tps=40.0),
        _report(tmp_path, "q4", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9), tps=95.0),
    ]
    row = compare(load_reports(paths))["performance"][0]
    assert row["promptWords"] == 128
    assert [entry["outputTokensPerS"] for entry in row["runs"]] == [40.0, 95.0]


def test_different_hardware_is_flagged_as_not_comparable(tmp_path):
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9)),
        _report(
            tmp_path, "b", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9),
            hardware="1x A100 80GB",
        ),
    ]
    warnings = compare(load_reports(paths))["warnings"]
    assert any("different hardware" in warning for warning in warnings)


def test_different_datasets_invalidate_the_quality_comparison(tmp_path):
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9)),
        _report(
            tmp_path, "b", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9),
            dataset_hash="sha256:other",
        ),
    ]
    warnings = compare(load_reports(paths))["warnings"]
    assert any("different datasets" in warning for warning in warnings)


def test_different_answer_styles_are_flagged(tmp_path):
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9)),
        _report(
            tmp_path, "b", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9), style="direct"
        ),
    ]
    assert any("Different answer styles" in w for w in compare(load_reports(paths))["warnings"])


def test_a_run_without_consistency_is_reported_as_not_measured(tmp_path):
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9)),
        _report(tmp_path, "b", quantization="Q4", accuracy=(0.8, 0.7, 200, 0.9)),
    ]
    assert all(row["measured"] is False for row in compare(load_reports(paths))["consistency"])


def test_different_datasets_leave_block_b_not_comparable(tmp_path):
    """Not only a warning: the category verdict itself says it is not compared."""
    paths = [
        _report(tmp_path, "a", quantization="FP16", accuracy=(0.9, 0.85, 200, 0.95)),
        _report(tmp_path, "b", quantization="Q4", accuracy=(0.5, 0.45, 200, 0.55),
                dataset_hash="sha256:other"),
    ]
    (row,) = compare(load_reports(paths))["capability"]
    assert row["verdict"]["status"] == "not-comparable"
    assert "dataset" in row["verdict"]["detail"] and "significant" not in row["verdict"]


# --- Agents (smith reports, D80) --------------------------------------------

def _rate(point, low, high, n=20):
    return {"measured": True, "point": point, "low": low, "high": high, "n": n}


def _smith(tmp_path, name, *, task=(0.5, 0.3, 0.7), scenarios="sha256:set", spec="0.3",
           classifier="sha256:tox", rf=0.9, level="hash-chained"):
    payload = {
        "kind": "smith-report", "specVersion": spec, "opaasVersion": "0.4.0",
        "agent": {"adapter": "http", "endpoint": f"http://{name}.test"},
        "repeats": 2, "certifiable": True,
        "scenarioSet": {"source": ["operator"], "hash": scenarios},
        "behaviour": {
            "taskCompleted": _rate(*task),
            "forbiddenAttempted": _rate(0.1, 0.0, 0.3),
            "injectionResisted": {"measured": False, "reason": "No run where this applies."},
            "agentErrors": 0,
        },
        "instability": _rate(0.0, 0.0, 0.2),
        "agentToxicity": {"measured": True, "classifierHash": classifier,
                          "aboveThresholdRate": _rate(0.0, 0.0, 0.2)},
        "agentBias": {"measured": False, "reason": "No scenario has counterfactual variants."},
        "auditability": {
            "ACR": _rate(0.8, 0.6, 0.9),
            "RF": {"measured": True, "point": rf, "n": 8},
            "SPDR": _rate(0.5, 0.3, 0.7),
            "AC": {"measured": True, "AC": _rate(0.5, 0.3, 0.7)},
            "IS": {"levels": [level], "weakest": level},
        },
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _figures(comparison):
    found = {f["metric"]: f for f in comparison["behaviour"]}
    found.update({m["metric"]: m for d in comparison["auditability"] for m in d["metrics"]})
    return found


def test_two_agent_runs_are_compared_on_behaviour_and_the_five_dimensions(tmp_path):
    comparison = compare(load_reports([_smith(tmp_path, "a"), _smith(tmp_path, "b")]))
    assert "opaasAgentComparison" in comparison
    assert [d["dimension"] for d in comparison["auditability"]] == [
        "actionRecoverability", "lifecycleCoverage", "policyCheckability",
        "responsibilityAttribution", "evidenceIntegrity"]
    figures = _figures(comparison)
    assert figures["taskCompleted"]["verdict"]["status"] == "no-difference"
    # Not measured in either run: never compared, never a zero.
    assert figures["injectionResisted"]["verdict"]["status"] == "not-measured"
    assert figures["LPC"]["runs"][0]["reason"] == "This report does not carry it."
    # No ranking, no total: the runs stay in the order given.
    assert [r["label"] for r in comparison["runs"]] == ["http · http://a.test",
                                                        "http · http://b.test"]
    assert not {"score", "total", "rank"} & set(comparison)


def test_agent_intervals_that_overlap_are_no_difference_and_apart_are_one(tmp_path):
    close = compare(load_reports([_smith(tmp_path, "a", task=(0.6, 0.4, 0.8)),
                                  _smith(tmp_path, "b", task=(0.45, 0.3, 0.65))]))
    assert _figures(close)["taskCompleted"]["verdict"]["status"] == "no-difference"
    apart = compare(load_reports([_smith(tmp_path, "c", task=(0.9, 0.75, 1.0)),
                                  _smith(tmp_path, "d", task=(0.2, 0.1, 0.4))]))
    verdict = _figures(apart)["taskCompleted"]["verdict"]
    assert verdict["status"] == "difference" and "do not overlap" in verdict["detail"]


def test_a_different_scenario_set_leaves_every_agent_figure_not_comparable(tmp_path):
    comparison = compare(load_reports([
        _smith(tmp_path, "a", task=(0.9, 0.75, 1.0)),
        _smith(tmp_path, "b", task=(0.2, 0.1, 0.4), scenarios="sha256:another")]))
    statuses = {f["verdict"]["status"] for f in _figures(comparison).values()}
    assert statuses == {"not-comparable"}
    assert "scenario set" in comparison["warnings"][0]


def test_a_different_spec_version_leaves_agent_figures_not_comparable(tmp_path):
    comparison = compare(load_reports([_smith(tmp_path, "a"), _smith(tmp_path, "b", spec="0.4")]))
    verdict = _figures(comparison)["ACR"]["verdict"]
    assert verdict["status"] == "not-comparable" and "spec version" in verdict["detail"]


def test_a_different_toxicity_detector_leaves_only_toxicity_not_comparable(tmp_path):
    comparison = compare(load_reports([_smith(tmp_path, "a"),
                                       _smith(tmp_path, "b", classifier="sha256:other")]))
    figures = _figures(comparison)
    assert figures["agentToxicity"]["verdict"]["status"] == "not-comparable"
    assert "detector hash" in figures["agentToxicity"]["verdict"]["detail"]
    assert figures["taskCompleted"]["verdict"]["status"] == "no-difference"


def test_a_figure_without_an_interval_claims_no_difference(tmp_path):
    """RF carries no interval: 0.9 against 0.4 is shown, never called a difference."""
    comparison = compare(load_reports([_smith(tmp_path, "a", rf=0.9, level="signed"),
                                       _smith(tmp_path, "b", rf=0.4, level="none")]))
    figures = _figures(comparison)
    assert figures["RF"]["verdict"]["status"] == "no-interval"
    assert "no difference is claimed" in figures["RF"]["verdict"]["detail"]
    assert figures["IS"]["verdict"]["status"] == "changed"


def test_reports_of_different_kinds_are_not_compared(tmp_path):
    model = _report(tmp_path, "m", quantization="FP16", accuracy=(0.8, 0.7, 200, 0.9))
    with pytest.raises(ComparisonError, match="different kinds"):
        load_reports([model, _smith(tmp_path, "a")])


# --- Systems (system reports, D80) ------------------------------------------

_DIMENSIONS = ("performance", "capability", "consistency", "safety", "compliance",
               "actionRecoverability", "lifecycleCoverage", "policyCheckability",
               "responsibilityAttribution", "evidenceIntegrity", "degradation")


def _system(tmp_path, name, *, accuracy=(0.5, 0.3, 0.7), measured=("capability",),
            dataset="sha256:ds", members=True, spec="0.3"):
    """A one-model system report, with its bench member written next to it."""
    directory = tmp_path / name
    directory.mkdir()
    source = "sha256:shared-member"
    member = []
    if members:
        bench = {"runId": "x", "opaasVersion": spec, "suites": [
            {"suite": "capability", "status": "completed",
             "metrics": {"dataset": {"datasetHash": dataset}, "answerStyle": "direct",
                         "tasks": [{"category": "reasoning", "accuracy": list(accuracy)}]}}]}
        path = directory / "bench-llm.json"
        path.write_text(json.dumps(bench), encoding="utf-8")
        source = file_digest(path)
        member = [{"component": "llm", "kind": "model-report", "file": "bench-llm.json",
                   "hash": source}]
    measurements = [
        {"component": "llm", "axis": "behaviour", "dimension": dimension,
         "aspect": "reasoning (smoke)", "metric": "accuracy", "status": "measured",
         "direction": "higher-is-better",
         "source": {"kind": "model-report", "file": "bench-llm.json", "hash": source},
         "value": {"type": "rate", "point": accuracy[0], "low": accuracy[1],
                   "high": accuracy[2], "n": 20}}
        for dimension in measured
    ]
    payload = {"kind": "system-report", "specVersion": spec, "createdAt": name,
               "system": {"id": "support", "manifestHash": "sha256:m"},
               "environmentFingerprint": {"bound": True, "hash": "sha256:env"},
               "certifiability": {"certifiable": False, "reasons": []},
               "members": member, "measurements": measurements,
               "coverage": {"llm": {d: "measured" if d in measured else "not-measured"
                                    for d in _DIMENSIONS}}}
    path = directory / "system-report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _cell(comparison, component, dimension):
    return next(c for c in comparison["cells"]
                if c["component"] == component and c["dimension"] == dimension)


def test_a_cell_measured_in_one_audit_only_is_a_coverage_change_not_a_difference(tmp_path):
    comparison = compare(load_reports([
        _system(tmp_path, "a"),
        _system(tmp_path, "b", measured=("capability", "safety"))]))
    assert "opaasSystemComparison" in comparison
    cell = _cell(comparison, "llm", "safety")
    assert cell["status"] == "coverage-changed" and "measurements" not in cell
    assert [s["state"] for s in cell["states"]] == ["not-measured", "measured"]
    assert _cell(comparison, "llm", "performance")["status"] == "unchanged"
    # Every row × dimension has its cell, compared or not.
    assert len(comparison["cells"]) == len(_DIMENSIONS)


def test_system_measurements_follow_the_overlap_rule(tmp_path):
    close = compare(load_reports([_system(tmp_path, "a"),
                                  _system(tmp_path, "b", accuracy=(0.6, 0.4, 0.8))]))
    (figure,) = _cell(close, "llm", "capability")["measurements"]
    assert figure["verdict"]["status"] == "no-difference"
    apart = compare(load_reports([_system(tmp_path, "c", accuracy=(0.9, 0.8, 1.0)),
                                  _system(tmp_path, "d", accuracy=(0.2, 0.1, 0.3))]))
    (figure,) = _cell(apart, "llm", "capability")["measurements"]
    assert figure["verdict"]["status"] == "difference"


def test_two_audits_citing_one_member_report_one_measurement_not_two(tmp_path):
    comparison = compare(load_reports([_system(tmp_path, "a", members=False),
                                       _system(tmp_path, "b", members=False)]))
    (figure,) = _cell(comparison, "llm", "capability")["measurements"]
    assert figure["verdict"]["status"] == "same-evidence"


def test_a_member_on_another_dataset_leaves_its_cell_not_comparable(tmp_path):
    comparison = compare(load_reports([
        _system(tmp_path, "a", accuracy=(0.9, 0.8, 1.0)),
        _system(tmp_path, "b", accuracy=(0.2, 0.1, 0.3), dataset="sha256:other")]))
    (figure,) = _cell(comparison, "llm", "capability")["measurements"]
    assert figure["verdict"]["status"] == "not-comparable"
    assert "dataset" in figure["verdict"]["detail"]


def test_a_member_that_cannot_be_read_is_named_not_passed_silently(tmp_path):
    first = _system(tmp_path, "a")
    (first.parent / "bench-llm.json").write_text("{}", encoding="utf-8")
    comparison = compare(load_reports([first, _system(tmp_path, "b")]))
    assert any("bench-llm.json could not be read" in w for w in comparison["warnings"])


def test_a_different_spec_version_leaves_system_cells_not_comparable(tmp_path):
    comparison = compare(load_reports([_system(tmp_path, "a"),
                                       _system(tmp_path, "b", spec="0.4")]))
    (figure,) = _cell(comparison, "llm", "capability")["measurements"]
    assert figure["verdict"]["status"] == "not-comparable"
