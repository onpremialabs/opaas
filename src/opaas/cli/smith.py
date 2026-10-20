"""`opaas smith`: scenarios with deterministic oracles."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Column, Table

from opaas.adapters import get_backend
from opaas.adapters.base import BackendError
from opaas.cli.common import (
    Force,
    _plain,
    console,
    err_console,
    recorded_run,
    refuse_overwrite,
)
from opaas.core.evidence import EvidenceError, envelope, read_document, write_document
from opaas.core.fingerprint import deployment_fingerprint
from opaas.core.report import describe_environment
from opaas.smith import (
    A2aAgent,
    BareModelAgent,
    DifyAgent,
    FlowiseAgent,
    HttpAgent,
    LangGraphAgent,
    LangServeAgent,
    McpAgent,
    N8nAgent,
    OpenWebUiAgent,
    ScenarioError,
    available_agents,
)
from opaas.smith.rerouted import DEFAULT_TOOLS_LISTEN, TOKEN_ENV, parse_listen
from opaas.smith.runner import build_report, run_scenarios
from opaas.smith.scenario import available_packs, load_pack, load_scenarios
from opaas.smith.toxicity import score_agent_toxicity

#: Adapters for agents whose tools the operator reroutes to OPAAS's mock server (D68, D69).
REROUTED = ("a2a", "dify", "flowise", "langgraph", "langserve", "n8n", "open-webui")

app = typer.Typer(help="Fixed scenarios, mock tools, deterministic oracles.",
                  no_args_is_help=True)

Scenarios = Annotated[str, typer.Option(
    "--scenarios", "-s", help="'reference', 'pack:<id>' (see `smith packs`), a directory of "
    "scenario JSON files, or one file.")]


@app.command("scenarios")
def scenarios_command(scenarios: Scenarios = "reference") -> None:
    """Lists and validates scenarios, with their hashes."""
    try:
        loaded = load_scenarios(scenarios)
    except ScenarioError as exc:
        err_console.print(f"[red]{_plain(exc)}[/red]")
        raise typer.Exit(code=2) from None
    table = Table("id", "category", "tools", "policies", "steps", "hash")
    for s in loaded:
        table.add_row(s.id, s.category, str(len(s.tools)), str(len(s.policies)),
                      str(s.max_steps), s.hash[:23] + "…")
    console.print(table)
    console.print(f"{len(loaded)} scenario(s) valid.")


@app.command("packs")
def packs_command() -> None:
    """Lists the domain scenario packs, each checked against its pinned hash (D71)."""
    table = Table(Column("pack", no_wrap=True), "version", "language", "scenarios",
                  "categories", "hash", "title")
    for pack_id in available_packs():
        try:
            pack, loaded = load_pack(pack_id)
        except ScenarioError as exc:
            err_console.print(f"[red]{_plain(exc)}[/red]")
            raise typer.Exit(code=1) from None
        table.add_row(f"pack:{pack.id}", str(pack.version), pack.language, str(len(loaded)),
                      ", ".join(sorted({s.category for s in loaded})), pack.hash[:23] + "…",
                      pack.title)
    console.print(table)
    console.print("Not certifiable on their own: a pack tests a domain, not this deployment. "
                  "Run them beside your own scenarios.")


@app.command("run")
def run_command(
    ctx: typer.Context,
    agent: Annotated[str, typer.Option("--agent", "-a",
                                       help=f"Adapter: {', '.join(available_agents())}.")],
    scenarios: Scenarios = "reference",
    repeats: Annotated[int, typer.Option("--repeats", "-n", min=1,
                                         help="Runs per scenario; 2+ measures instability.")]
    = 3,
    endpoint: Annotated[str | None, typer.Option(
        "--endpoint", help="Agent URL (http, mcp and the rerouted adapters; for dify the "
        "app's API base with /v1, for n8n the Chat Trigger's webhook URL).")] = None,
    tools_listen: Annotated[str, typer.Option(
        "--tools-listen", help="host:port where OPAAS serves the scenario's mock tools over "
        "MCP (a2a, dify, flowise, langgraph, langserve, n8n, open-webui). The agent must "
        "be configured to use "
        "http://<host:port>/mcp as its only tools, before the run.")] = DEFAULT_TOOLS_LISTEN,
    assistant: Annotated[str | None, typer.Option(
        "--assistant", help="LangGraph assistant or graph id (default 'agent'), or the "
        "Flowise chatflow id (required).")] = None,
    agent_token: Annotated[str | None, typer.Option(
        "--agent-token", envvar=TOKEN_ENV, help="The agent's API key (the rerouted adapters; "
        "dify requires the app's key). Never written to the report.")] = None,
    backend: Annotated[str | None, typer.Option(
        "--backend", "-b", help="Engine adapter (bare-model).")] = None,
    model: Annotated[str | None, typer.Option(
        "--model", "-m", help="Model (bare-model), or the Open WebUI model or agent id.")]
    = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Engine URL.")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", envvar="OPAAS_API_KEY",
                                                help="Engine key.")] = None,
    inventory: Annotated[Path | None, typer.Option(
        "--inventory", help="`discover scan` document to bind the result to (N5).")] = None,
    toxicity_model: Annotated[Path | None, typer.Option(
        "--toxicity-model", help="Toxicity classifier weights for the agent's answers; "
        "without it the pinned default is used if the classifiers extra is installed.")]
    = None,
    toxicity: Annotated[bool, typer.Option(
        "--toxicity/--no-toxicity", help="Score the agent's final answers for toxicity.")]
    = True,
    keep_finals: Annotated[bool, typer.Option(
        "--keep-finals", help="Write the agent's final answers into the report. By default "
        "only their digest and length are written (N4): the report is a public document.")]
    = False,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("smith.json"),
    force: Force = False,
) -> None:
    """Runs scenarios against an agent: behaviour and auditability, per category, no score."""
    refuse_overwrite(out, force)
    try:
        loaded = load_scenarios(scenarios)
        inventory_doc = read_document(inventory, kind="inventory")["inventory"] \
            if inventory else None
    except (ScenarioError, EvidenceError) as exc:
        err_console.print(f"[red]{_plain(exc)}[/red]")
        raise typer.Exit(code=2) from None

    environment: dict[str, Any] = {}
    if inventory_doc:
        environment["inventory"] = inventory_doc
    engine = None
    if agent in ("http", "mcp"):
        if not endpoint:
            err_console.print(f"[red]--agent {agent} needs --endpoint.[/red]")
            raise typer.Exit(code=2)
        adapter: Any = HttpAgent(endpoint) if agent == "http" else McpAgent(endpoint)
    elif agent in REROUTED:
        if not endpoint:
            err_console.print(f"[red]--agent {agent} needs --endpoint.[/red]")
            raise typer.Exit(code=2)
        if agent == "open-webui" and not model:
            err_console.print("[red]--agent open-webui needs --model: the Open WebUI model or "
                              "agent id whose tools are OPAAS's mock server.[/red]")
            raise typer.Exit(code=2)
        if agent == "flowise" and not assistant:
            err_console.print("[red]--agent flowise needs --assistant: the Flowise chatflow id "
                              "whose tools are OPAAS's mock server.[/red]")
            raise typer.Exit(code=2)
        if agent == "dify" and not agent_token:
            err_console.print(f"[red]--agent dify needs the app's API key, in {TOKEN_ENV} or "
                              "--agent-token.[/red]")
            raise typer.Exit(code=2)
        try:
            parse_listen(tools_listen)
        except ValueError as exc:
            err_console.print(f"[red]--tools-listen: {_plain(exc)}[/red]")
            raise typer.Exit(code=2) from None
        common: dict[str, Any] = {"tools_listen": tools_listen, "token": agent_token}
        if agent == "open-webui":
            adapter = OpenWebUiAgent(endpoint, model=str(model), **common)
        elif agent == "langgraph":
            adapter = LangGraphAgent(endpoint, assistant=assistant or "agent", **common)
        elif agent == "flowise":
            adapter = FlowiseAgent(endpoint, assistant=str(assistant), **common)
        else:
            adapter = {"a2a": A2aAgent, "dify": DifyAgent, "langserve": LangServeAgent,
                       "n8n": N8nAgent}[agent](endpoint, **common)
        console.print(f"[dim]Mock tools at {adapter.tools_url}: the agent must use them, and "
                      "only them (D68).[/dim]")
    elif agent == "bare-model":
        if not backend or not model:
            err_console.print("[red]--agent bare-model needs --backend and --model.[/red]")
            raise typer.Exit(code=2)
        try:
            engine = get_backend(backend)
            engine.load(model, {"base_url": base_url, "api_key": api_key})
        except BackendError as exc:
            err_console.print(f"[red]{_plain(exc)}[/red]")
            raise typer.Exit(code=1) from None
        environment["deployment"] = deployment_fingerprint(
            engine, engine.get_backend_metadata(), describe_environment())
        adapter = BareModelAgent(engine)
    else:
        err_console.print(f"[red]Unknown agent adapter '{agent}'. "
                          f"Known: {', '.join(available_agents())}.[/red]")
        raise typer.Exit(code=2)

    with recorded_run(ctx, "smith", endpoint=endpoint or base_url) as recorded:
        try:
            def progress(scenario: str, done: int, total: int) -> None:
                console.print(f"[dim]{done}/{total} {scenario}[/dim]")
                recorded.progress(scenario, phase="scenarios", index=done, total=total,
                                  percent=100.0 * done / total if total else None)

            outcomes = run_scenarios(loaded, adapter, repeats=repeats, keep_finals=keep_finals,
                                     progress=progress)
        except OSError as exc:  # the mock tools' address is taken or not ours
            err_console.print(f"[red]Cannot serve the mock tools at {tools_listen}: "
                              f"{_plain(exc)}[/red]")
            raise typer.Exit(code=2) from None
        finally:
            adapter.close()
            if engine is not None:
                engine.close()
        answers = [o.final_text for o in outcomes if o.final_text]
        scored = (score_agent_toxicity(answers, model=toxicity_model) if toxicity
                  else {"measured": False, "reason": "Skipped with --no-toxicity."})
        report = build_report(loaded, adapter, outcomes, repeats=repeats, toxicity=scored,
                              keep_finals=keep_finals)
        digest = write_document(out, envelope("smith-report", report, environment=environment))
        recorded.keep(out)
    _print(report)
    console.print(f"\n{out} ({digest})")


def _fmt(metric: dict[str, Any]) -> str:
    if not metric.get("measured", True):
        return f"[dim]not measured: {_plain(metric.get('reason'))}[/dim]"
    if metric.get("point") is None:
        return "—"
    low, high = metric.get("low"), metric.get("high")
    interval = f" [{low:.2f}, {high:.2f}]" if low is not None and high is not None else ""
    return f"{metric['point']:.2f}{interval} (n={metric.get('n')})"


def _print(report: dict[str, Any]) -> None:
    console.print("\n[bold]Behaviour[/bold] (against the mock-tool ground truth)")
    for name, metric in report["behaviour"].items():
        if isinstance(metric, dict):
            console.print(f"  {name:<32} {_fmt(metric)}")
    console.print("\n[bold]Auditability[/bold] (the agent's own record)")
    audit = report["auditability"]
    for name in ("ACR", "RF", "LPC", "GB", "SPDR"):
        if name in audit:
            console.print(f"  {name:<32} {_fmt(audit[name])}")
    if "AC" in audit and audit["AC"].get("measured", True) and "AC" in audit["AC"]:
        console.print(f"  {'AC':<32} {_fmt(audit['AC']['AC'])}")
    if audit.get("misleadingVerdicts"):
        console.print(f"  [red]misleading record[/red] for {audit['misleadingVerdicts']}")
    console.print(f"\n  instability {_fmt(report['instability'])}")
    console.print("\n[bold]Agent-level bias and toxicity[/bold]")
    bias = report.get("agentBias") or {}
    if bias.get("measured"):
        console.print(f"  {'counterfactual divergence':<32} {_fmt(bias['divergentRate'])}")
        for row in bias["scenarios"]:
            reason = f" — {_plain(row['reason'])}" if row.get("reason") else ""
            console.print(f"    {_plain(row['scenario'])} ({row['attribute']}): "
                          f"{row['verdict']}{reason}")
    else:
        console.print(f"  {'counterfactual divergence':<32} {_fmt(bias)}")
    toxicity = report.get("agentToxicity") or {}
    shown = toxicity["aboveThresholdRate"] if toxicity.get("measured") else toxicity
    console.print(f"  {'toxic answers':<32} {_fmt(shown)}")
    for line in report["limitations"]:
        console.print(f"[yellow]limitation:[/yellow] {_plain(line)}")
