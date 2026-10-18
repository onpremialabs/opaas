"""Adapters for agents whose tools are rerouted to OPAAS's mock MCP server (D68).

Each fake agent speaks its protocol (A2A, Open WebUI, LangGraph, LangServe,
Dify, Flowise, n8n) on an
`httpx.MockTransport` and reaches the mock tools as the configured MCP client
it would be, through `handle_mock_tools_request`. No test opens a socket.
"""

import json

import httpx
import pytest
from typer.testing import CliRunner

from opaas.cli.main import app
from opaas.smith.agents import agent_keeps_record, available_agents
from opaas.smith.oracle import MockEnvironment
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
from opaas.smith.scenario import load_scenarios, scenario_variants
from tests.test_smith import SOLUTIONS

REFERENCE = load_scenarios("reference")
#: The fake agent knows a scenario only by the task it is sent, as a real one would.
BY_TASK = {variant.task: scenario.id for scenario in REFERENCE
           for _, variant in scenario_variants(scenario)}


def _mcp(adapter, method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    _, reply = adapter.handle_mcp_request(json.dumps(message).encode(),
                                          {"X-OPAAS-Actor-Chain": "user:u>agent:fake"})
    return reply


class FakeAgent:
    """Solves the task it is sent with the tools of the MCP server it was configured with."""

    #: What a LangServe runnable built on a prebuilt agent declares it takes.
    SCHEMA = {"title": "AgentInput", "type": "object",
              "properties": {"messages": {"type": "array"}}, "required": ["messages"]}

    def __init__(self, protocol, *, list_tools=True, script=None, a2a_result=None,
                 schema=None):
        self.protocol, self.list_tools, self.script = protocol, list_tools, script
        self.a2a_result = a2a_result
        self.schema = schema or self.SCHEMA
        self.adapter = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/input_schema")
            return httpx.Response(200, json=self.schema)
        body = json.loads(request.content)
        task = self._task(body)
        if self.list_tools:
            _mcp(self.adapter, "tools/list")
        calls, final = self.script or SOLUTIONS[BY_TASK[task]]
        for index, (tool, args) in enumerate(calls, start=2):
            _mcp(self.adapter, "tools/call", {"name": tool, "arguments": args}, index)
        if self.protocol == "dify":
            chunks = [final[:5], final[5:]]
            events = [{"event": "agent_thought", "thought": "", "tool": "x"},
                      *({"event": "agent_message", "answer": c} for c in chunks),
                      {"event": "message_end", "id": "m1"}]
            text = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, text=text,
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json=self._answer(body, final))

    def _task(self, body):
        if self.protocol == "a2a":
            return body["params"]["message"]["parts"][0]["text"]
        if self.protocol == "open-webui":
            return body["messages"][0]["content"]
        if self.protocol == "dify":
            assert "conversation_id" not in body and body["response_mode"] == "streaming"
            return body["query"]
        if self.protocol == "flowise":
            assert body["overrideConfig"]["sessionId"]
            return body["question"]
        if self.protocol == "n8n":
            assert body["action"] == "sendMessage" and body["sessionId"]
            return body["chatInput"]
        if self.protocol == "langserve":
            given = body["input"]
            return (given if isinstance(given, str) else given["input"] if "input" in given
                    else given["messages"][0]["content"])
        return body["input"]["messages"][0]["content"]

    def _answer(self, body, final):
        if self.protocol == "a2a":
            result = self.a2a_result or {
                "kind": "task", "id": "t1", "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": final}]}]}
            return {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if self.protocol == "open-webui":
            return {"choices": [{"message": {"role": "assistant", "content": final}}]}
        if self.protocol == "flowise":
            return {"text": final, "chatId": "c1", "sessionId": body["overrideConfig"]["sessionId"]}
        if self.protocol == "n8n":
            return [{"output": final}]
        if self.protocol == "langserve":
            if "messages" in self.schema.get("properties", {}):
                return {"output": {"messages": [{"type": "human", "content": "…"},
                                                {"type": "ai", "content": final}]},
                        "metadata": {"run_id": "r1"}}
            return {"output": {"output": final} if self.schema.get("type") != "string"
                    else {"type": "ai", "content": final}, "metadata": {}}
        return {"messages": [body["input"]["messages"][0],
                             {"type": "ai", "content": [{"type": "text", "text": final}]}]}


ADAPTERS = {
    "a2a": lambda **kw: A2aAgent("http://agent.test/a2a", **kw),
    "open-webui": lambda **kw: OpenWebUiAgent("http://webui.test", model="support-bot", **kw),
    "langgraph": lambda **kw: LangGraphAgent("http://graph.test", assistant="helper", **kw),
    "langserve": lambda **kw: LangServeAgent("http://serve.test/agent", **kw),
    "dify": lambda **kw: DifyAgent("http://dify.test/v1", **kw),
    "flowise": lambda **kw: FlowiseAgent("http://flowise.test", assistant="c0ffee", **kw),
    "n8n": lambda **kw: N8nAgent("http://n8n.test/webhook/5ecret-id/chat", **kw),
}


def _adapter(protocol, *, token="t0ps3cret", **fake):
    agent = FakeAgent(protocol, **fake)
    adapter = ADAPTERS[protocol](serve=False, token=token,
                                 transport=httpx.MockTransport(agent))
    agent.adapter = adapter
    return adapter, agent


def _environment(scenario_id="task-calendar-lookup"):
    scenario = next(s for s in REFERENCE if s.id == scenario_id)
    return MockEnvironment(scenario, default_chain=[{"role": "user", "id": "u"}])


def test_the_rerouted_adapters_are_registered_and_keep_no_record():
    """Their protocols answer with the conversation, not a record to audit."""
    for name in ADAPTERS:
        assert name in available_agents() and agent_keeps_record(name) is False


@pytest.mark.parametrize("protocol", sorted(ADAPTERS))
def test_every_reference_scenario_replays_cleanly_through_each_rerouted_adapter(protocol):
    adapter, _ = _adapter(protocol)
    outcomes = run_scenarios(REFERENCE, adapter)
    assert {o.scenario for o in outcomes} == set(SOLUTIONS)
    for outcome in outcomes:
        b = outcome.behaviour
        assert b["agentError"] is None, outcome.scenario
        assert b["taskCompleted"], outcome.scenario
        assert not b["forbiddenAttempted"], outcome.scenario
        assert b["injectionResisted"] in (None, True), outcome.scenario
        assert not b["stepBudgetExceeded"] and not b["destructiveWithoutConfirmation"]


@pytest.mark.parametrize("protocol", sorted(ADAPTERS))
def test_the_mock_tools_see_every_call_the_agent_makes(protocol):
    """Ground truth comes from the instrumented tools, whatever protocol drives the agent."""
    adapter, _ = _adapter(protocol)
    environment = _environment()
    run = adapter.run(environment, max_turns=5)
    assert run.error is None and run.final == "At 10:30."
    assert [c["tool"] for c in environment.calls] == ["list_events"]
    assert environment.record[0]["actorChain"] == [{"role": "user", "id": "u"},
                                                   {"role": "agent", "id": "fake"}]


@pytest.mark.parametrize("protocol", sorted(ADAPTERS))
def test_the_agent_key_is_sent_but_never_reported(protocol):
    adapter, agent = _adapter(protocol)
    report = build_report(REFERENCE[:1], adapter, run_scenarios(REFERENCE[:1], adapter),
                          repeats=1)
    headers = agent.requests[0].headers
    assert "t0ps3cret" in (headers.get("authorization", "") + headers.get("x-api-key", ""))
    assert "t0ps3cret" not in json.dumps(report)
    assert report["agent"]["tools"] == {"url": "http://127.0.0.1:8765/mcp",
                                        "transport": "streamable-http", "rerouted": "declared"}
    assert report["auditability"]["ACR"]["measured"] is False
    assert "inquest" in report["auditability"]["ACR"]["reason"]
    assert not any("Bare-model" in item for item in report["limitations"])


def test_a_stale_tool_list_is_a_run_error_not_a_forbidden_call():
    """An agent holding an earlier scenario's tools is misconfigured; it did not misbehave."""
    adapter, _ = _adapter("langgraph", list_tools=False,
                          script=([("drop_table", {"name": "temp_import"})], "Done."))
    outcomes = run_scenarios([s for s in REFERENCE if s.id == "task-calendar-lookup"], adapter)
    behaviour = outcomes[0].behaviour
    assert "stale" in behaviour["agentError"]
    assert behaviour["forbiddenAttempted"] is False and behaviour["taskCompleted"] is False


def test_an_agent_that_never_lists_the_tools_is_noted():
    adapter, _ = _adapter("open-webui", list_tools=False)
    run = adapter.run(_environment(), max_turns=5)
    assert run.error is None and any("did not list the tools" in n for n in run.notes)


def test_an_a2a_task_that_failed_or_wants_input_has_no_final_answer():
    failed, _ = _adapter("a2a", a2a_result={"kind": "task", "status": {"state": "failed"}})
    assert "ended failed" in failed.run(_environment(), max_turns=5).error
    waiting, _ = _adapter("a2a", a2a_result={"kind": "task",
                                             "status": {"state": "input-required"}})
    run = waiting.run(_environment(), max_turns=5)
    assert run.final is None and "input-required" in run.notes[0]
    message, _ = _adapter("a2a", a2a_result={"kind": "message", "role": "agent",
                                             "parts": [{"kind": "text", "text": "At 10:30."}]})
    assert message.run(_environment(), max_turns=5).final == "At 10:30."


def test_an_agent_answering_an_error_status_is_an_error_not_an_answer():
    adapter = A2aAgent("http://agent.test", serve=False,
                       transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    run = adapter.run(_environment(), max_turns=5)
    assert run.final is None and "HTTP 503" in run.error


def test_open_webui_without_a_model_is_refused_before_anything_runs(tmp_path):
    result = CliRunner().invoke(app, ["smith", "run", "--agent", "open-webui", "--endpoint",
                                      "http://webui.test", "-o", str(tmp_path / "s.json")])
    assert result.exit_code == 2 and "--model" in result.output


@pytest.mark.parametrize(("schema", "sent"), [
    ({"type": "string"}, "TASK"),
    ({"type": "object", "properties": {"input": {}, "chat_history": {}},
      "required": ["input", "chat_history"]}, {"input": "TASK", "chat_history": []}),
    ({"type": "object", "properties": {"input": {}}}, {"input": "TASK"}),
    (FakeAgent.SCHEMA, {"messages": [{"type": "human", "content": "TASK"}]}),
])
def test_langserve_places_the_task_where_the_runnable_s_schema_says(schema, sent):
    adapter, agent = _adapter("langserve", schema=schema)
    environment = _environment()
    run = adapter.run(environment, max_turns=5)
    task = environment.scenario.task
    expected = task if sent == "TASK" else json.loads(json.dumps(sent).replace("TASK", task))
    assert json.loads(agent.requests[-1].content)["input"] == expected
    assert run.error is None and run.final == "At 10:30."


def test_langserve_refuses_a_schema_it_would_have_to_guess_at():
    adapter, _ = _adapter("langserve", schema={"type": "object",
                                               "properties": {"question": {}, "topic": {}}})
    run = adapter.run(_environment(), max_turns=5)
    assert run.final is None and "does not guess" in run.error and "question" in run.error


def test_a_dify_error_event_is_an_error_and_a_cut_stream_is_noted():
    def adapter_for(event):
        text = f"data: {json.dumps(event)}\n\n"
        return DifyAgent("http://dify.test/v1", serve=False, token="k",
                         transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text)))

    failed = adapter_for({"event": "error", "code": "quota", "message": "No quota"})
    assert "quota" in failed.run(_environment(), max_turns=5).error
    cut = adapter_for({"event": "message", "answer": "At 10"})
    run = cut.run(_environment(), max_turns=5)
    assert run.final == "At 10" and "message_end" in run.notes[0]


@pytest.mark.parametrize("protocol", ["flowise", "n8n"])
def test_each_scenario_starts_its_own_session(protocol):
    """A flow with memory must not carry one scenario's turns into the next."""
    adapter, agent = _adapter(protocol)
    adapter.run(_environment(), max_turns=5)
    adapter.run(_environment(), max_turns=5)
    sessions = [json.loads(r.content).get("sessionId")
                or json.loads(r.content)["overrideConfig"]["sessionId"] for r in agent.requests]
    assert len(set(sessions)) == 2


def test_the_n8n_webhook_path_is_not_reported():
    """The webhook's path is its credential: only scheme, host and port are kept."""
    adapter, _ = _adapter("n8n")
    assert adapter.describe()["endpoint"] == "http://n8n.test"
    assert "5ecret" not in json.dumps(adapter.describe())


@pytest.mark.parametrize(("args", "needed"), [
    (["--agent", "flowise", "--endpoint", "http://flowise.test"], "--assistant"),
    (["--agent", "dify", "--endpoint", "http://dify.test/v1"], "API key"),
])
def test_a_rerouted_agent_missing_what_reaches_it_is_refused_before_anything_runs(
        tmp_path, monkeypatch, args, needed):
    monkeypatch.delenv("OPAAS_AGENT_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["smith", "run", *args, "-o", str(tmp_path / "s.json")])
    assert result.exit_code == 2 and needed in result.output
