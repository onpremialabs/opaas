"""Drafting a system manifest from what `discover` observes (D60, phase S3).

The operator should not have to type what the host already says: every model
an engine serves, every MCP server and tool in the configurations named to
`discover`. The draft declares exactly those, so that auditing it on the same
host finds every declared component observed and nothing observed undeclared.

**Agents are drafted from what identifies them** (D63): an agent that
publishes an A2A card or names its framework, found by `discover`, with the
adapter smith will use for it (pending until smith has one); and, given the
records of a running system (`--records`), every agent that acted in them,
the models and tools it called and the flows between them. With records, only
the models actually used are declared; the others an engine serves are
noted, not drafted.

**One environment, when the scan was scoped to a folder** (D85): only the
models its files name and an engine here serves, and only its agents that are
running, are drafted. Everything else the host serves is counted in a note,
not drafted; an agent the folder declares that is not running, and a model it
names that no engine here serves, are noted too.

**A draft is not a declaration.** It writes down what is there, which is not
the same as what the system is: an engine may serve a model no application
uses, and an agent nothing identifies and that left no records cannot be
drafted at all. The purpose is left as a marker the system report
refuses to certify until a person replaces it. What the draft could not
decide comes back as notes, never as guesses in the manifest.
"""

from __future__ import annotations

from typing import Any

from opaas.core.hashing import slugify
from opaas.system.manifest import FORMAT, REROUTED_ADAPTERS, REROUTED_REQUIRES

#: The purpose until the operator writes it. The system report refuses to call a
#: system certifiable while it is still there: the AI Act risk classification
#: follows the intended use, and only a person can state it.
PURPOSE_PLACEHOLDER = (
    "TO COMPLETE: the intended use of this system. The AI Act risk classification "
    "(art. 6, annex III) depends on it."
)

#: Discover's engine kind → the adapter that reaches it.
_BACKENDS = {"ollama": "ollama", "vllm": "vllm", "openai-compatible": "openai"}


def draft_manifest(
    inventory: dict[str, Any],
    *,
    system_id: str = "system",
    purpose: str | None = None,
    records: list[dict[str, Any]] | None = None,
    records_source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """(manifest, notes): the components the inventory and records show, and what was not decided.

    `records`: AuditEvent records of the running system (as `inquest` imports
    them); `records_source`: the manifest's `records` entry pointing at them.
    """
    components: list[dict[str, Any]] = []
    notes: list[str] = []
    taken: set[str] = set()
    usage = _usage(records) if records is not None else None
    scoped = inventory.get("project")
    wanted: set[str] = set()
    for model in (scoped or {}).get("models") or []:
        if model.get("servedBy"):
            wanted |= {str(model["name"]), f"{model['name']}:latest"}
    outside = 0

    def unique(base: str, suffix: str) -> str:
        candidate = (slugify(base) or "component")[:56]
        if candidate in taken:
            candidate = f"{candidate}-{slugify(suffix)}"[:60]
        number = 2
        stem = candidate
        while candidate in taken:
            candidate, number = f"{stem}-{number}", number + 1
        taken.add(candidate)
        return candidate

    for engine in inventory.get("engines") or []:
        backend = _BACKENDS.get(str(engine.get("kind")))
        if backend is None:
            notes.append(f"Engine {engine.get('kind')} at {engine.get('endpoint')} has no "
                         "adapter: its models were not drafted.")
            continue
        for model in engine.get("models") or []:
            name = str(model.get("name") or "")
            if not name:
                continue
            if scoped is not None and name not in wanted:
                outside += 1
                continue
            if usage is not None and name not in usage["models"]:
                notes.append(f"{name} is served by {engine.get('kind')} and was not called in "
                             "the records: not drafted.")
                continue
            components.append({
                "id": unique(name, str(engine.get("kind"))),
                "kind": "model",
                "backend": backend,
                "model": name,
                "base_url": f"http://{engine.get('endpoint')}",
            })
    stack = inventory.get("agentStack") or {}
    model_ids = {c["model"]: c["id"] for c in components if c["kind"] == "model"}
    agent_ids: dict[str, str] = {}
    running = {a.get("running") for a in (scoped or {}).get("agents") or [] if a.get("running")}
    for agent in stack.get("agentEndpoints") or []:
        if scoped is not None and agent.get("endpoint") not in running:
            outside += 1
            continue
        component_id = unique(str(agent.get("name") or agent.get("kind")), str(agent["kind"]))
        agent_ids[str(agent.get("name"))] = component_id
        components.append({"id": component_id, "kind": "agent", "adapter": agent["adapter"],
                           "endpoint": f"http://{agent['endpoint']}",
                           "name": str(agent.get("name")),
                           "description": f"Identified by {agent.get('basis')}."})
        if agent["adapter"] in REROUTED_ADAPTERS:
            notes.append(
                f"{component_id}: point its tools at OPAAS's mock MCP server and declare "
                "'tools_listen' (host:port) before auditing"
                + (f", and its '{required[0]}' ({required[1]})"
                   if (required := REROUTED_REQUIRES.get(agent["adapter"])) else "")
                + ". Until then smith does not drive it: its scenarios would act on its "
                "real tools (D68).")
    proxy = None
    if usage is not None:
        if records_source and records_source.get("source") == "sentinel":
            proxy = unique("sentinel", "proxy")
            components.append({"id": proxy, "kind": "proxy", "sentinel": True})
        for actor in sorted(usage["agents"]):
            if actor in agent_ids:
                continue
            agent_ids[actor] = unique(actor, "agent")
            components.append({"id": agent_ids[actor], "kind": "agent", "adapter": "observed",
                               "name": actor,
                               "description": "Known from the records it left."})
        tool_ids = {}
        for tool in sorted(usage["tools"]):
            tool_ids[tool] = unique(tool, "tool")
            components.append({"id": tool_ids[tool], "kind": "tool", "name": tool})
        for model in sorted(usage["models"] - set(model_ids)):
            notes.append(f"{model} is called in the records and no engine on this host serves "
                         "it: it may be remote (add --endpoint) or gone. Not drafted.")
    for config in stack.get("mcpConfigs") or []:
        for server in config.get("servers") or []:
            if server.get("name"):
                components.append({"id": unique(str(server["name"]), "mcp"),
                                   "kind": "mcp-server", "name": str(server["name"])})
    for manifest in stack.get("toolManifests") or []:
        for tool in manifest.get("tools") or []:
            if tool.get("name"):
                components.append({"id": unique(str(tool["name"]), "tool"), "kind": "tool",
                                   "name": str(tool["name"])})
    flows: list[dict[str, str]] = []
    if usage is not None:
        known = {**model_ids, **tool_ids}
        for actor, target in sorted(usage["edges"]):
            if actor in agent_ids and target in known:
                flow = {"from": agent_ids[actor], "to": known[target]}
                if proxy and target in model_ids:
                    flow["via"] = proxy
                flows.append(flow)
    if scoped is not None:
        notes.extend(_scope_notes(scoped, outside))
    for runtime in [] if scoped is not None else stack.get("agentRuntimes") or []:
        notes.append(
            f"An agent runtime is running ({runtime.get('name') or runtime.get('process')}). "
            "Declare it as an agent component with the adapter and endpoint that reach it: "
            "nothing on the host says which."
        )
    for item in inventory.get("unidentified") or []:
        notes.append(f"Port {item.get('port')} ({item.get('process')}) looks like an engine "
                     "and did not answer as one: not drafted.")
    if not components:
        notes.append("Nothing was observed to draft: name the engines with --endpoint, the MCP "
                     "configurations with --config and the tool manifests with --manifest.")
    if usage is None:
        notes.append("Flows and the models each agent uses come from records: pass the "
                     "sentinel's log with --records to draft them.")
    notes.append("Scenarios, the declaration and thresholds are the operator's: add them "
                 "before auditing.")
    if purpose is None:
        notes.append("The purpose is a marker: the system is not certifiable until a person "
                     "writes the intended use.")
    manifest = {
        "format": FORMAT,
        "id": slugify(system_id) or "system",
        "purpose": purpose or PURPOSE_PLACEHOLDER,
        "components": components,
        "flows": flows,
    }
    if records_source:
        manifest["records"] = [{**records_source, **({"component": proxy} if proxy else {})}]
    return manifest, notes


def update_manifest(existing: dict[str, Any],
                    drafted: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(manifest, notes): `existing`, with what a new draft observed and it lacked added.

    **An update only adds** (D86). Everything a person wrote stays as written:
    the purpose (unless it is still the marker and the new draft has one), the
    flows, records, thresholds, declaration and scenarios, and every component,
    including the ones the host no longer shows; `audit run` reports those as
    declared and not observed, which is what they are. A component is new when
    no existing one has its kind and what identifies it (model, endpoint or
    name).
    """
    manifest = dict(existing)
    components = [dict(c) for c in existing.get("components") or [] if isinstance(c, dict)]
    known = {_identity(c) for c in components}
    taken = {str(c.get("id")) for c in components}
    notes: list[str] = []
    for component in drafted.get("components") or []:
        if _identity(component) in known:
            continue
        component = dict(component)
        stem, number = str(component["id"]), 2
        while component["id"] in taken:
            component["id"], number = f"{stem}-{number}", number + 1
        taken.add(component["id"])
        components.append(component)
        notes.append(f"Added {component['kind']} {component['id']}: newly observed.")
    observed = {_identity(c) for c in drafted.get("components") or []}
    for component in components:
        if _identity(component) not in observed:
            notes.append(f"Kept {component.get('kind')} {component.get('id')}: declared and not "
                         "observed now; the audit will say so.")
    unwritten = (None, "", PURPOSE_PLACEHOLDER)
    if manifest.get("purpose") in unwritten and drafted.get("purpose") not in unwritten:
        manifest["purpose"] = drafted["purpose"]
    manifest["components"] = components
    if not any(n.startswith("Added ") for n in notes):
        notes.insert(0, "Nothing new observed: the manifest's components are unchanged.")
    return manifest, notes


def _identity(component: dict[str, Any]) -> tuple[str, str]:
    """What makes two components the same one, whatever their ids."""
    kind = str(component.get("kind"))
    for field in ("model", "endpoint", "name"):
        if component.get(field):
            return kind, f"{field}:{component[field]}"
    return kind, f"id:{component.get('id')}"


def _scope_notes(scoped: dict[str, Any], outside: int) -> list[str]:
    """What a folder-scoped draft left out, and why (D85)."""
    notes = []
    for agent in scoped.get("agents") or []:
        if not agent.get("running"):
            notes.append(f"{agent['kind']} agent '{agent['name']}' is declared in "
                         f"{agent['file']} and is not running on this host: start it, then "
                         "scan again. Not drafted.")
    for model in scoped.get("models") or []:
        if not model.get("servedBy"):
            notes.append(f"{model['name']} is named in the project and no engine here serves "
                         "it: pull it, or name its engine with --endpoint. Not drafted.")
    if not scoped.get("models"):
        notes.append(f"The project '{scoped.get('root')}' names no model in the files OPAAS "
                     "reads (a model named only in code is not seen): add the models its agents "
                     "use as model components.")
    if outside:
        notes.append(f"{outside} model(s) and agent(s) this host serves are not the project's: "
                     "not drafted.")
    return notes


def _usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Agents, models and tools that appear in the records, and who called what."""
    agents: set[str] = set()
    models: set[str] = set()
    tools: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for record in records:
        chain = [h for h in record.get("actorChain") or [] if isinstance(h, dict)]
        agents |= {str(h.get("id")) for h in chain if h.get("role") == "agent" and h.get("id")}
        target, action = record.get("target"), record.get("action")
        if not target:
            continue
        if action == "model_call":
            models.add(str(target))
        elif action == "tool_call":
            tools.add(str(target))
        else:
            continue
        caller = next((h for h in reversed(chain) if h.get("role") == "agent"), None)
        if caller and caller.get("id"):
            edges.add((str(caller["id"]), str(target)))
    return {"agents": agents, "models": models, "tools": tools, "edges": edges}


__all__ = ["PURPOSE_PLACEHOLDER", "draft_manifest", "update_manifest"]
