"""`smith`: fixed scenarios with deterministic oracles (detect + ground truth, D47).

How does this agent behave, and how auditable is it, in scenarios whose
correct outcome is known? A scenario's mock tools are instrumented: every
call is recorded with all its fields, which makes them the ground truth the
paper's end-to-end audit was missing. Behaviour is judged against that ground
truth with structural predicates only, and the agent's own record is measured
against it on all five dimensions.

- `scenario`: the JSON scenario format, validation and hashing;
- `oracle`: the mock-tool environment that answers and records;
- `agents`: adapters for the agent under test (bare model, HTTP, MCP);
- `rerouted`: adapters for agents whose tools are pointed at OPAAS's mock MCP
  server (A2A, Open WebUI, LangGraph, D68; LangServe, Dify, Flowise, n8n, D69);
- `runner`: repeats, behaviour and auditability metrics, the report.
"""

from opaas.smith.agents import (
    AgentAdapter,
    BareModelAgent,
    HttpAgent,
    McpAgent,
    available_agents,
)
from opaas.smith.rerouted import (
    A2aAgent,
    DifyAgent,
    FlowiseAgent,
    LangGraphAgent,
    LangServeAgent,
    N8nAgent,
    OpenWebUiAgent,
)
from opaas.smith.runner import build_report, run_scenarios
from opaas.smith.scenario import ScenarioError, load_scenarios

__all__ = [
    "A2aAgent",
    "AgentAdapter",
    "BareModelAgent",
    "DifyAgent",
    "FlowiseAgent",
    "HttpAgent",
    "LangGraphAgent",
    "LangServeAgent",
    "McpAgent",
    "N8nAgent",
    "OpenWebUiAgent",
    "ScenarioError",
    "available_agents",
    "build_report",
    "load_scenarios",
    "run_scenarios",
]
