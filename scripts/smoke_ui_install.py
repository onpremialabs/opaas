"""Drive the dashboard's flow from an installed `opaas[ui]`, against test doubles (phase 24).

    uv build --wheel
    uv venv /tmp/clean
    uv pip install --python /tmp/clean "opaas[ui] @ file://$PWD/dist/opaas-X.Y.Z-py3-none-any.whl"
    /tmp/clean/bin/python -I scripts/smoke_ui_install.py --installed

`tests/test_ui.py` runs from the source tree, where every template, stylesheet,
scenario pack and dataset is on disk whether or not the wheel ships it. This
runs the same flow from an installed package: a file the wheel leaves out, or
a dependency the `ui` extra forgot, shows up here and not there.

The flow is phase 22's, end to end:

1. two model runs, an agent run (`smith`, bare model, a domain pack) and a
   system audit, each recorded in a fresh run store by the CLI in process,
   against fake backends defined below (no network, no GPU, no weights);
2. a sentinel event log and an approval queue;
3. the dashboard over that store, through Starlette's in-process client: the
   overview, the history, *New audit*, each run page, the sentinel page, the
   static files and the meerkat;
4. two jobs launched as the dashboard launches them, as real child processes
   of `python -m opaas`: a draft certificate of a model run, and a
   comparison of the two model runs; then their run pages;
5. a signing command refused by the dashboard.

`--installed` refuses to run against the source tree, so CI cannot pass by
importing the checkout. Without it the script runs anywhere, which is how
`tests/test_ui_install_smoke.py` keeps it from rotting. Exit 0 when every step
did what it should, 1 when one did not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

TOKEN = "smoke-token"
HOST = "127.0.0.1:8780"
#: The answer of the agent double: it declines every task, which is a valid final answer.
DECLINE = '{"final": "I cannot tell."}'


class SmokeFailure(RuntimeError):
    """A step that did not do what the dashboard promises."""


def _check(condition: bool, what: str) -> None:
    if not condition:
        raise SmokeFailure(what)
    print(f"  ok  {what}")


def _backends() -> tuple[type, type]:
    """The doubles of `tests/conftest.py`, redefined: tests are not part of the wheel."""
    from opaas.adapters.base import GenerationResult, InferenceBackend, ResourceMetrics

    class SmokeBackend(InferenceBackend):
        name: ClassVar[str] = "smoke"
        engine_label: ClassVar[str] = "Smoke Engine"
        default_base_url: ClassVar[str] = "memory://smoke"

        def load(self, model_id: str, config: dict[str, Any]) -> None:
            self._model_id = model_id

        def answer(self, prompt: str, max_tokens: int) -> str:
            return "response " * max(1, max_tokens // 8)

        def generate(self, prompt: str, **params: Any) -> GenerationResult:
            max_tokens = int(params.get("max_tokens", 128))
            return GenerationResult(
                text=self.answer(prompt, max_tokens), prompt_tokens=max(1, len(prompt.split())),
                completion_tokens=max_tokens, total_s=0.004, ttft_s=0.002, finish_reason="stop")

        def get_resource_usage(self) -> ResourceMetrics:
            return ResourceMetrics(available=False, note="smoke backend")

        def get_backend_metadata(self) -> dict[str, Any]:
            return {"adapter": self.name, "engine": self.engine_label,
                    "backend": "Smoke Engine 0.0"}

    class DecliningBackend(SmokeBackend):
        name: ClassVar[str] = "smoke-declining"

        def answer(self, prompt: str, max_tokens: int) -> str:
            return DECLINE

    return SmokeBackend, DecliningBackend


def _cli(*args: str) -> None:
    from typer.testing import CliRunner

    from opaas.cli.main import app

    result = CliRunner().invoke(app, list(args))
    if result.exit_code != 0:
        raise SmokeFailure(f"`opaas {' '.join(args)}` exited {result.exit_code}:\n"
                           f"{result.output}")


def _latest(kind: str) -> str:
    from opaas.core.store import list_runs

    return max(r["runId"] for r in list_runs() if r["kind"] == kind)


def _record(work: Path) -> dict[str, Any]:
    """Step 1 and 2: the runs and the sentinel files the dashboard will read."""
    from opaas.adapters import register_backend
    from opaas.core.events import AuditEvent, EventLog, evidence, parse_actor_chain
    from opaas.sentinel.gate import ApprovalQueue

    smoke, declining = _backends()
    register_backend(smoke)
    register_backend(declining)
    print("Recording runs with the CLI, against fake backends")
    models = []
    for quantization in ("Q8_0", "Q4_K_M"):
        _cli("run", "--model", "smoke/model-1b", "--backend", "smoke", "--suite", "capability",
             "--dataset", "opaas-smoke", "--max-tokens", "8", "--quantization", quantization)
        models.append(_latest("bench"))
    _cli("smith", "run", "--agent", "bare-model", "--backend", "smoke-declining", "--model", "m",
         "-n", "1", "--no-toxicity", "-s", "pack:banking", "-o", str(work / "agent.json"))
    agent = _latest("smith")
    system = work / "system.json"
    system.write_text(json.dumps({
        "format": 1, "id": "support", "purpose": "Answers employees from documents.",
        "components": [
            {"id": "llm", "kind": "model", "backend": "smoke", "model": "smoke/model-1b"},
            {"id": "agent", "kind": "agent", "adapter": "bare-model",
             "backend": "smoke-declining", "model": "m"}],
        "flows": [{"from": "agent", "to": "llm"}]}), encoding="utf-8")
    _cli("audit", "run", str(system), "-o", str(work / "audit"), "--skip", "discover",
         "--bench-args", "--suite capability --dataset opaas-smoke --max-tokens 8",
         "-n", "1", "--no-open")
    audit = _latest("audit")
    _check(True, f"model runs {models[0]} and {models[1]}, agent {agent}, system {audit}")

    log = EventLog(work / "events.jsonl")
    chain = parse_actor_chain("user:alice>agent:planner")
    log.append(AuditEvent(action="tool_call", target="delete_file", actor_chain=chain,
                          args=evidence({"path": "/etc"}), output=evidence("")).to_record())
    queue = work / "approvals"
    ApprovalQueue(queue).submit({"event": "e1", "target": "delete_file", "actorChain": chain,
                                 "args": evidence({"path": "/etc"}),
                                 "policies": [{"policy": "confirm-delete", "verdict": "violate"}]})
    return {"models": models, "agent": agent, "audit": audit, "log": log.path, "queue": queue}


def _pages(client: Any, runs: dict[str, Any]) -> None:
    """Step 3: every screen, built from the store by the installed templates."""
    print("Reading the dashboard")
    _check(runs["models"][0] in client.get("/").text, "the overview lists the runs")
    history = client.get("/runs").text
    _check(all(r in history for r in (*runs["models"], runs["agent"], runs["audit"]))
           and "data-compare-pick" in history, "the history lists every run, to tick")
    new = client.get("/new").text
    _check('value="pack:banking"' in new and 'id="launch-system"' in new
           and f"{runs['models'][0]} · bench" in new,
           "New audit offers the packs, the system form and stored runs to cite")
    model = client.get(f"/runs/{runs['models'][0]}").text
    _check("<h1>smoke/model-1b</h1>" in model and 'class="ibar"' in model
           and "matches run.json" in model and "Generate draft v1" in model,
           "a model run shows its intervals, its checked files and its draft button")
    agent = client.get(f"/runs/{runs['agent']}").text
    _check("Behaviour" in agent and "Task completed" in agent and "pack:banking" in agent
           and "Generate draft" not in agent,
           "an agent run shows its behaviour and offers no draft of its own")
    system = client.get(f"/runs/{runs['audit']}").text
    _check("<h1>System · support</h1>" in system and "cell-measured" in system
           and "<h3>Q1 · " in system and "agent → llm" in system,
           "a system run shows its coverage matrix, its card and its composition")
    sentinel = client.get("/sentinel", params={"log": str(runs["log"]),
                                               "queue": str(runs["queue"])}).text
    _check("held by confirm-delete" in sentinel and "opaas sentinel approve" in sentinel
           and "/etc" not in sentinel,
           "the sentinel page reads the log and the queue, digests only")
    for path, kind in (("/static/opaas.css", "text/css"), ("/static/app.js", "javascript"),
                       ("/favicon.svg", "image/svg+xml"), ("/mascot.json", "json")):
        response = client.get(path)
        _check(response.status_code == 200 and kind in response.headers["content-type"],
               f"{path} is served from the package")


def _job(client: Any, args: list[str], timeout: float = 120.0) -> dict[str, Any]:
    response = client.post("/api/jobs", json={"args": args}, headers={"x-opaas-token": TOKEN})
    if response.status_code != 201:
        raise SmokeFailure(f"`opaas {' '.join(args)}` was refused: {response.text}")
    job_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.2)
    else:
        raise SmokeFailure(f"`opaas {' '.join(args)}` did not finish in {timeout:.0f} s.")
    if job["exitCode"] != 0 or not job["run"]:
        raise SmokeFailure(f"`opaas {' '.join(args)}` ended {job['status']}, exit "
                           f"{job['exitCode']}, run {job['run']}:\n{job['log']}")
    return job


def _jobs(client: Any, store: Path, runs: dict[str, Any]) -> None:
    """Step 4 and 5: what the dashboard launches, and what it will not."""
    print("Launching jobs as child processes, as the dashboard does")
    first, second = runs["models"]
    draft = _job(client, ["certificate", "--run", first])
    directory = store / draft["run"]["runId"]
    _check(draft["run"]["kind"] == "certificate" and draft["run"]["status"] == "completed"
           and (directory / "certificate-v1.html").is_file(),
           "a draft certificate job records version 1 as a run of its own")
    reports = [str(store / run / "report.json") for run in (first, second)]
    compared = _job(client, ["compare", "--report", reports[0], "--report", reports[1]])
    _check(compared["run"]["kind"] == "compare" and compared["run"]["status"] == "completed",
           "a comparison job records the result of opaas compare")
    page = client.get(f"/runs/{compared['run']['runId']}").text
    _check("Comparison · 2 runs" in page and "in the order given, not ranked" in page
           and "certifies nothing" in page, "the comparison page ranks nothing")
    refused = client.post("/api/jobs", json={"args": ["sign", "--input", reports[0]]},
                          headers={"x-opaas-token": TOKEN})
    _check(refused.status_code == 409, "signing is refused: it stays in a terminal")


def smoke(store: Path, work: Path) -> None:
    """Steps 1 to 5 against `store`, which must be empty; raises `SmokeFailure`."""
    from starlette.testclient import TestClient

    from opaas.ui.app import COOKIE, create_app
    from opaas.ui.jobs import JobManager

    previous = os.environ.get("OPAAS_RUNS_DIR")
    os.environ["OPAAS_RUNS_DIR"] = str(store)
    try:
        runs = _record(work)
        app = create_app(store, token=TOKEN, hosts={HOST}, jobs=JobManager(store=store))
        with TestClient(app, base_url=f"http://{HOST}") as client:
            client.cookies.set(COOKIE, TOKEN)
            _pages(client, runs)
            _jobs(client, store, runs)
    finally:
        if previous is None:
            os.environ.pop("OPAAS_RUNS_DIR", None)
        else:
            os.environ["OPAAS_RUNS_DIR"] = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--installed", action="store_true",
                        help="Refuse to run against the source tree.")
    parser.add_argument("--workdir", type=Path,
                        help="Where the store and inputs go (a temporary directory if omitted).")
    options = parser.parse_args(argv)

    import opaas

    location = Path(opaas.__file__).resolve()
    source = Path(__file__).resolve().parent.parent / "src"
    print(f"opaas {opaas.__version__} from {location.parent}")
    if options.installed and source in location.parents:
        print("This is the source tree, not an installed package. Run it with `python -I` "
              "from an environment where only the wheel is installed.", file=sys.stderr)
        return 1
    try:
        import starlette.testclient  # noqa: F401
    except ImportError as exc:
        print(f"FAIL the ui extra is not installed ({exc}). Install `opaas[ui]`.",
              file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="opaas-ui-smoke-") as scratch:
        work = options.workdir or Path(scratch)
        work.mkdir(parents=True, exist_ok=True)
        try:
            smoke(work / "runs", work)
        except SmokeFailure as failure:
            print(f"FAIL {failure}", file=sys.stderr)
            return 1
    print("The dashboard's flow works from this install.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
