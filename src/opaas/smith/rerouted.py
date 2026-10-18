"""Adapters for agents OPAAS does not own, whose tools are rerouted to its mock MCP server (D68).

The `http` and `mcp` adapters hand the agent the mock tools' address with
each task: the agent was written, or wrapped, to take it. An agent built on
A2A, Open WebUI or LangGraph takes no such thing. Its tools are configured
beforehand, by whoever runs it. So the ground truth (N3) survives in one way
only: **the operator configures the agent's tools to be OPAAS's mock MCP
server**, at an address they choose (`--tools-listen`), and OPAAS serves the
current scenario's tools there for the whole run. Every call the agent makes
then goes through `MockEnvironment.call`, exactly as with the other adapters,
and behaviour is measured in full.

**An agent whose tools cannot be rerouted is not driven.** Its tools are real:
a destructive or injection scenario sent to it would delete, send or leak for
real, on the system under audit. Its behaviour stays *not measured* with that
reason; its records, if the operator names them, are audited by `inquest`.

**What OPAAS cannot see from here.** That the agent has no other tool besides
OPAAS's: the rerouting is the operator's declaration, and the report says so
(`tools.rerouted: "declared"`). That the agent read *this* scenario's tools:
an agent that keeps a tool list from an earlier scenario calls tools that no
longer exist. When the agent never listed the tools during a run, the run
says so in its notes, and a call to a tool the scenario does not have is then
reported as a run error, never counted as a forbidden call.

**No record of their own.** These protocols answer with the conversation, not
with a log of tool calls, so `keeps_record` is False and smith's auditability
metrics are not applicable; records the agent writes elsewhere are audited on
the row of the component that keeps them.

Every adapter takes an injectable `transport` and `serve=False`, so tests
drive a fake agent that reaches the mock tools through
`handle_mock_tools_request`, and no test opens a socket.
"""

from __future__ import annotations

import os
import uuid
from abc import abstractmethod
from typing import Any, ClassVar

import httpx

from opaas.smith.agents import (
    _REGISTRY,
    AgentAdapter,
    AgentError,
    AgentRun,
    _mcp_handler_for,
    _ToolServer,
    handle_mock_tools_request,
)
from opaas.smith.oracle import MockEnvironment

#: Where the mock tools listen when the operator does not say. Loopback: an
#: agent on another host needs the operator to choose an address it can reach.
DEFAULT_TOOLS_LISTEN = "127.0.0.1:8765"

#: Environment variable holding the agent's API key. Read, never recorded.
TOKEN_ENV = "OPAAS_AGENT_TOKEN"


def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not port.isdigit() or not host:
        raise ValueError(f"'{value}' is not host:port, e.g. {DEFAULT_TOOLS_LISTEN}.")
    return host.strip("[]"), int(port)


def _public_endpoint(url: str) -> str:
    parsed = httpx.URL(url)
    # Scheme, host and port only: a path or query can carry a key.
    return f"{parsed.scheme}://{parsed.host}" + (f":{parsed.port}" if parsed.port else "")


class ReroutedAgent(AgentAdapter):
    """An agent driven through its own conversation API, its tools pointed at OPAAS."""

    keeps_record: ClassVar[bool] = False

    def __init__(self, endpoint: str, *, tools_listen: str = DEFAULT_TOOLS_LISTEN,
                 timeout_s: float = 300.0, token: str | None = None,
                 transport: httpx.BaseTransport | None = None, serve: bool = True) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.tools_host, self.tools_port = parse_listen(tools_listen)
        self.timeout_s = timeout_s
        self.token = token if token is not None else os.environ.get(TOKEN_ENV)
        self.transport = transport
        self.serve = serve
        self._environment: MockEnvironment | None = None
        self._server: _ToolServer | None = None

    @property
    def tools_url(self) -> str:
        host = f"[{self.tools_host}]" if ":" in self.tools_host else self.tools_host
        return f"http://{host}:{self.tools_port}/mcp"

    def describe(self) -> dict[str, Any]:
        return {"adapter": self.name, "endpoint": _public_endpoint(self.endpoint),
                "tools": {"url": self.tools_url, "transport": "streamable-http",
                          "rerouted": "declared"},
                **self._describe_extra()}

    def _describe_extra(self) -> dict[str, Any]:
        return {}

    def no_record_reason(self) -> str:
        return (f"The {self.name} protocol answers with the conversation, not with a record "
                "of the agent's tool calls: records the agent writes elsewhere are audited "
                "by inquest, on the row of the component that keeps them (D68).")

    def handle_mcp_request(self, body: bytes, headers: dict[str, str]) -> tuple[int, Any]:
        """OPAAS's mock tools for the scenario running now. Pure: the server wraps it."""
        return handle_mock_tools_request(self._environment, body, headers)

    def run(self, environment: MockEnvironment, *, max_turns: int) -> AgentRun:
        if self.serve and self._server is None:
            # One server for the whole run, at the address the agent was told:
            # the environment behind it changes with each scenario.
            self._server = _ToolServer(handler=_mcp_handler_for(self.handle_mcp_request),
                                       host=self.tools_host, port=self.tools_port)
        self._environment = environment
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                run = self.converse(client, environment.scenario.task)
        except (httpx.HTTPError, AgentError, ValueError) as exc:
            run = AgentRun(final=None, error=f"The {self.name} agent could not be used: {exc}")
        finally:
            self._environment = None
        return _check_tool_list(run, environment)

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @abstractmethod
    def converse(self, client: httpx.Client, task: str) -> AgentRun:
        """Sends the task as the user's message; the agent's final answer."""


def _check_tool_list(run: AgentRun, environment: MockEnvironment) -> AgentRun:
    if environment.listed or not environment.calls:
        return run
    run.notes.append("The agent did not list the tools during this run: it may be using the "
                     "tool list of an earlier scenario.")
    stale = sorted({c["tool"] for c in environment.calls if not c["declared"]})
    if stale and run.error is None:
        run.stale_tools = True
        run.error = (f"The agent called {', '.join(stale)}, which this scenario does not have, "
                     "without listing its tools: its tool list is stale. Configure it to list "
                     "the tools of each new MCP session.")
    return run


def _post_json(client: httpx.Client, url: str, payload: Any,
               headers: dict[str, str]) -> Any:
    response = client.post(url, json=payload, headers={"Accept": "application/json", **headers})
    if response.status_code != 200:
        raise AgentError(f"the agent answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise AgentError("the agent's answer is not JSON") from None


# ------------------------------------------------------------------------- a2a


class A2aAgent(ReroutedAgent):
    """An A2A agent: JSON-RPC `message/send` to the URL its agent card names.

    Asks for a blocking answer. The final text is the task's artifacts, else
    its status message, else the message the agent answered with. A task the
    agent leaves needing input, or reports failed, has no final answer.
    """

    name: ClassVar[str] = "a2a"

    def converse(self, client: httpx.Client, task: str) -> AgentRun:
        body = _post_json(client, self.endpoint, {
            "jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {
                "message": {"role": "user", "messageId": uuid.uuid4().hex,
                            "parts": [{"kind": "text", "text": task}]},
                "configuration": {"blocking": True, "acceptedOutputModes": ["text/plain"]},
            },
        }, self._headers())
        if not isinstance(body, dict):
            raise AgentError("the agent sent no JSON-RPC answer")
        if body.get("error"):
            error = body["error"] if isinstance(body["error"], dict) else {}
            raise AgentError(f"the agent answered error {error.get('code')}: "
                             f"{error.get('message')}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise AgentError("the agent's JSON-RPC answer has no result")
        if result.get("kind", "task") == "message" or "parts" in result:
            return AgentRun(final=_parts_text(result.get("parts")), turns=1)
        state = str((result.get("status") or {}).get("state") or "")
        texts = [_parts_text(a.get("parts")) for a in result.get("artifacts") or []
                 if isinstance(a, dict)]
        final = "\n".join(t for t in texts if t) or _parts_text(
            ((result.get("status") or {}).get("message") or {}).get("parts"))
        if state in ("failed", "rejected", "canceled"):
            return AgentRun(final=None, error=f"The A2A task ended {state}.")
        if state in ("input-required", "auth-required"):
            return AgentRun(final=None, notes=[f"The A2A task stopped {state}: smith sends one "
                                               "message per scenario and answers nothing."])
        if state and state != "completed":
            return AgentRun(final=final or None,
                            notes=[f"The A2A task was still {state} when it answered."])
        return AgentRun(final=final or None, turns=1)


def _parts_text(parts: Any) -> str:
    """Text parts joined; `kind` from A2A 0.2 on, `type` before it."""
    return "\n".join(
        str(p.get("text", "")) for p in parts or []
        if isinstance(p, dict) and (p.get("kind") or p.get("type")) == "text"
    )


# ------------------------------------------------------------------ open-webui


class OpenWebUiAgent(ReroutedAgent):
    """Open WebUI's chat API: `/api/chat/completions` with the model (or agent) id.

    The tools are the MCP server the operator added to Open WebUI and enabled
    for that model; the key comes from OPAAS_AGENT_TOKEN and is never recorded.
    """

    name: ClassVar[str] = "open-webui"

    def __init__(self, endpoint: str, *, model: str, **kwargs: Any) -> None:
        super().__init__(endpoint, **kwargs)
        self.model = model

    def _describe_extra(self) -> dict[str, Any]:
        return {"model": self.model}

    def converse(self, client: httpx.Client, task: str) -> AgentRun:
        body = _post_json(client, f"{self.endpoint}/api/chat/completions", {
            "model": self.model, "stream": False,
            "messages": [{"role": "user", "content": task}],
        }, self._headers())
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AgentError("the answer has no choices[0].message.content") from None
        return AgentRun(final=None if content is None else str(content), turns=1)


# ------------------------------------------------------------------- langgraph


class LangGraphAgent(ReroutedAgent):
    """A LangGraph server (Agent Server API): a stateless run, `/runs/wait`.

    The input is the message list LangGraph's prebuilt agents take; the final
    answer is the last message of the state the run ends in.
    """

    name: ClassVar[str] = "langgraph"

    def __init__(self, endpoint: str, *, assistant: str = "agent", **kwargs: Any) -> None:
        super().__init__(endpoint, **kwargs)
        self.assistant = assistant

    def _describe_extra(self) -> dict[str, Any]:
        return {"assistant": self.assistant}

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.token} if self.token else {}

    def converse(self, client: httpx.Client, task: str) -> AgentRun:
        body = _post_json(client, f"{self.endpoint}/runs/wait", {
            "assistant_id": self.assistant,
            "input": {"messages": [{"role": "user", "content": task}]},
        }, self._headers())
        values = body.get("values", body) if isinstance(body, dict) else None
        messages = values.get("messages") if isinstance(values, dict) else None
        if not isinstance(messages, list) or not messages:
            raise AgentError("the run's state has no messages")
        return AgentRun(final=_message_text(messages[-1]),
                        turns=sum(1 for m in messages if _role(m) in ("ai", "assistant")))


def _role(message: Any) -> str | None:
    return (message.get("type") or message.get("role")) if isinstance(message, dict) else None


def _message_text(message: Any) -> str | None:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") == "text") or None
    return None


_REGISTRY.update({"a2a": A2aAgent, "langgraph": LangGraphAgent,
                  "open-webui": OpenWebUiAgent})


__all__ = [
    "DEFAULT_TOOLS_LISTEN",
    "TOKEN_ENV",
    "A2aAgent",
    "LangGraphAgent",
    "OpenWebUiAgent",
    "ReroutedAgent",
    "parse_listen",
]
