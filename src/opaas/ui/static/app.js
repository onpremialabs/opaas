// OPAAS dashboard. No framework and nothing loaded from elsewhere: the page's
// CSP allows only this file. It shows commands, launches them through the API,
// and follows their progress. It measures nothing (docs/dashboard.md §2).
"use strict";

const TOKEN = document.querySelector('meta[name="opaas-token"]').content;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"content-type": "application/json", "x-opaas-token": TOKEN},
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function launch(args, extra) {
  const job = await api("/api/jobs", {args, ...(extra || {})});
  location.href = `/jobs/${job.id}`;
  return job;
}

// --- Copy and one-click launches -------------------------------------------
document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    await navigator.clipboard.writeText(copy.dataset.copy).catch(() => {});
    copy.textContent = "Copied";
    setTimeout(() => (copy.textContent = "Copy"), 1200);
  }
  const button = event.target.closest("[data-launch]");
  if (button) {
    button.disabled = true;
    try { await launch(JSON.parse(button.dataset.launch)); }
    catch (error) { button.disabled = false; alert(error.message); }
  }
});

// --- New audit forms: the command is built, shown, then sent as is ---------
function benchArgs(form) {
  const f = new FormData(form);
  const args = ["run", "--model", (f.get("model") || "").trim(), "--backend", f.get("backend")];
  const url = (f.get("base_url") || "").trim();
  if (url) args.push("--base-url", url);
  const ctx = (f.get("num_ctx") || "").trim();
  if (ctx) args.push("--num-ctx", ctx);
  const dataset = (f.get("dataset") || "").trim();
  if (dataset) args.push("--dataset", dataset);
  const suites = f.getAll("suite");
  for (const s of suites.includes("all") ? ["all"] : suites) args.push("--suite", s);
  return args;
}
// Which fields each agent adapter needs (smith/agents.py, smith/rerouted.py).
function agentFields(adapter, rerouted) {
  if (adapter === "bare-model") return ["backend", "model", "base_url"];
  const fields = ["endpoint"];
  if (adapter === "open-webui") fields.push("model");
  if (adapter === "langgraph" || adapter === "flowise") fields.push("assistant");
  return fields;
}
function agentArgs(form) {
  const f = new FormData(form);
  const adapter = f.get("agent");
  const rerouted = form.getAttribute("data-rerouted").split(",");
  const args = ["smith", "run", "--agent", adapter];
  const value = (name) => (f.get(name) || "").trim();
  for (const field of agentFields(adapter, rerouted)) {
    const flag = {endpoint: "--endpoint", backend: "--backend", model: "--model",
                  base_url: "--base-url", assistant: "--assistant"}[field];
    if (value(field)) args.push(flag, value(field));
  }
  const scenarios = f.get("scenarios") === "path" ? value("path") : f.get("scenarios");
  if (scenarios) args.push("--scenarios", scenarios);
  if (value("repeats")) args.push("-n", value("repeats"));
  return args;
}
function agentProblem(form) {
  const f = new FormData(form);
  const adapter = f.get("agent");
  const rerouted = form.getAttribute("data-rerouted").split(",");
  const has = (name) => (f.get(name) || "").trim() !== "";
  if (adapter === "bare-model" && !has("model")) return "Name the model the bare agent runs on.";
  if (adapter !== "bare-model" && !has("endpoint")) return "Enter the agent's URL.";
  if (adapter === "open-webui" && !has("model")) return "Enter the Open WebUI model or agent id.";
  if (adapter === "flowise" && !has("assistant")) return "Enter the Flowise chatflow id.";
  if (f.get("scenarios") === "path" && !has("path")) return "Enter the scenario directory.";
  if (rerouted.includes(adapter) && !f.get("confirm"))
    return "Confirm that the agent's only tools are OPAAS's mock server.";
  return null;
}
function systemArgs(form) {
  const f = new FormData(form);
  const value = (name) => (f.get(name) || "").trim();
  const args = ["audit", "run", value("manifest")];
  for (const step of f.getAll("skip")) args.push("--skip", step);
  if (value("bench_args")) args.push("--bench-args", value("bench_args"));
  if (value("repeats")) args.push("-n", value("repeats"));
  const components = f.getAll("member_component");
  const paths = f.getAll("member_run");
  components.forEach((component, i) => {
    if (component.trim() && paths[i]) args.push("--member", `${component.trim()}=${paths[i]}`);
  });
  return args;
}
function discoverArgs(form) {
  const f = new FormData(form);
  const args = ["discover", "scan"];
  const endpoint = (f.get("endpoint") || "").trim();
  if (endpoint) args.push("--endpoint", endpoint);
  if (f.get("well_known")) args.push("--well-known");
  return args;
}
// Only forms that launch something: the sentinel page's path form is a plain GET form.
for (const form of document.querySelectorAll("form.launch[data-kind]")) {
  // getAttribute, not form.dataset: a control named "dataset" shadows it on a form.
  const kind = form.getAttribute("data-kind");
  const build = {bench: benchArgs, agent: agentArgs, system: systemArgs,
                 discover: discoverArgs}[kind];
  const preview = form.querySelector("[data-preview]");
  const error = form.querySelector("[data-error]");
  const refresh = () => {
    preview.textContent = "$ opaas " + build(form).join(" ");
    if (kind === "agent") {
      const adapter = form.querySelector('[name="agent"]').value;
      const rerouted = form.getAttribute("data-rerouted").split(",");
      const shown = agentFields(adapter, rerouted);
      for (const label of form.querySelectorAll("[data-for]")) {
        const field = label.getAttribute("data-for");
        const visible = field === "path"
          ? form.querySelector('[name="scenarios"]').value === "path"
          : shown.includes(field);
        label.classList.toggle("hidden", !visible);
      }
      form.querySelector("[data-rerouted-box]").classList.toggle(
        "hidden", !rerouted.includes(adapter));
    }
    const warn = form.querySelector('[data-warn="ollama-ctx"]');
    if (warn) {
      const f = new FormData(form);
      warn.classList.toggle("hidden", !(f.get("backend") === "ollama" && !f.get("num_ctx")));
    }
  };
  form.addEventListener("input", refresh);
  if (kind === "system") {
    const rows = form.querySelector("[data-members]");
    const template = form.querySelector("[data-member-row]");
    form.querySelector("[data-add-member]").addEventListener("click", () => {
      rows.insertBefore(template.content.cloneNode(true), template);
      refresh();
    });
    rows.addEventListener("click", (event) => {
      if (event.target.closest("[data-remove-member]")) {
        event.target.closest(".member").remove();
        refresh();
      }
    });
    // Draft and validate are their own commands, on the same manifest path.
    for (const button of form.querySelectorAll("[data-system]")) {
      button.addEventListener("click", async () => {
        const f = new FormData(form);
        const manifest = f.get("manifest").trim();
        if (!manifest) {
          error.textContent = "Enter the path of the system manifest.";
          error.classList.remove("hidden");
          return;
        }
        const args = button.getAttribute("data-system") === "draft"
          ? ["audit", "draft", "-o", manifest]
          : ["audit", "validate", manifest];
        const records = (f.get("records") || "").trim();
        if (args[1] === "draft" && records) args.push("--records", records);
        try { await launch(args); }
        catch (e) { error.textContent = e.message; error.classList.remove("hidden"); }
      });
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const args = build(form);
    if (kind === "bench" && !args[2]) {
      error.textContent = "Enter the model id exactly as the backend serves it.";
      error.classList.remove("hidden");
      return;
    }
    const problem = kind === "agent" ? agentProblem(form)
      : kind === "system" && !new FormData(form).get("manifest").trim()
        ? "Enter the path of the system manifest." : null;
    if (problem) {
      error.textContent = problem;
      error.classList.remove("hidden");
      return;
    }
    const confirmed = kind === "agent" && Boolean(new FormData(form).get("confirm"));
    try { await launch(args, confirmed ? {confirmRerouted: true} : undefined); }
    catch (e) { error.textContent = e.message; error.classList.remove("hidden"); }
  });
  refresh();
}

// --- New audit: discover-first picker ---------------------------------------
// `/new` scans the machine with a real, recorded `discover scan` job (never a
// bare probe from the server itself), then offers what it found as checkboxes
// for one or many models, one or many agents, or the whole-system form below.
const discoverBox = document.querySelector("[data-discover]");
if (discoverBox) {
  const body = discoverBox.querySelector("[data-discover-body]");
  const sub = discoverBox.querySelector("[data-discover-sub]");
  const rescanBtn = discoverBox.querySelector("[data-rescan]");
  const pick = document.querySelector("[data-pick]");

  const launchScan = async () => {
    body.innerHTML = '<p class="dim">Scanning…</p>';
    sub.textContent = "Scanning this machine for engines and agents…";
    rescanBtn.hidden = true;
    let job;
    try { job = await api("/api/jobs", {args: ["discover", "scan", "--well-known"]}); }
    catch (e) { sub.textContent = `Could not start the scan: ${e.message}`; rescanBtn.hidden = false; return; }
    const poll = async () => {
      const status = await api(`/api/jobs/${job.id}`).catch(() => null);
      if (!status) return setTimeout(poll, 1500);
      if (status.run && status.run.status !== "running") {
        loadPicker(status.run.runId);
      } else if (["queued", "running"].includes(status.status)) {
        setTimeout(poll, 1000);
      } else {
        sub.textContent = "The scan did not complete. Use the form below, or rescan.";
        rescanBtn.hidden = false;
      }
    };
    poll();
  };

  const loadPicker = async (runId) => {
    const data = await api(`/api/discover/${runId}`).catch(() => null);
    rescanBtn.hidden = false;
    if (!data) {
      sub.textContent = "No inventory from that scan. Use the form below instead.";
      body.innerHTML = "";
      return;
    }
    renderPicker(data);
  };

  const modelLabel = (engine, model) => {
    const label = document.createElement("label");
    label.className = "inline";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.pickModel = "1";
    input.disabled = !engine.backend;
    input.dataset.payload = JSON.stringify(
      {model: model.name, backend: engine.backend, baseUrl: engine.baseUrl});
    label.append(input, document.createTextNode(" " + model.name + " "));
    if (model.quantization) {
      const small = document.createElement("span");
      small.className = "dim mono";
      small.textContent = model.quantization;
      label.append(small);
    }
    if (!engine.backend) {
      label.append(document.createTextNode(" (no matching backend)"));
    }
    return label;
  };

  const agentLabel = (agent) => {
    const label = document.createElement("label");
    label.className = "inline";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.pickAgent = "1";
    input.disabled = !agent.runnable;
    input.dataset.payload = JSON.stringify(
      {endpoint: agent.endpoint, adapter: agent.adapter, rerouted: agent.rerouted});
    label.append(input, document.createTextNode(` ${agent.name || agent.kind} · ${agent.adapter} `));
    const small = document.createElement("span");
    small.className = "dim mono";
    small.textContent = agent.endpoint || "";
    label.append(small);
    if (!agent.runnable) label.append(document.createTextNode(" (no matching adapter yet)"));
    return label;
  };

  const renderPicker = (data) => {
    const engines = data.engines || [];
    const agents = data.agents || [];
    const totalModels = engines.reduce((n, e) => n + (e.models || []).length, 0);
    sub.textContent = engines.length || agents.length
      ? `${engines.length} engine(s), ${totalModels} model(s), ${agents.length} agent(s) found.`
      : "Nothing found listening on this machine.";
    body.innerHTML = "";
    for (const engine of engines) {
      const card = document.createElement("div");
      card.className = "card";
      const h3 = document.createElement("h3");
      h3.textContent = `${engine.kind} · :${engine.port} · ${engine.version || "?"} · ${engine.bind}`;
      card.append(h3);
      if (engine.models.length) {
        for (const model of engine.models) card.append(modelLabel(engine, model));
      } else {
        const p = document.createElement("p");
        p.className = "dim";
        p.textContent = "No models reported.";
        card.append(p);
      }
      body.append(card);
    }
    if (agents.length) {
      const card = document.createElement("div");
      card.className = "card";
      const h3 = document.createElement("h3");
      h3.textContent = "Agents";
      card.append(h3);
      for (const agent of agents) card.append(agentLabel(agent));
      body.append(card);
    }
    if (!engines.length && !agents.length) {
      body.innerHTML = '<p class="dim">Nothing found. Use the form below to point OPAAS at a '
        + "remote engine or agent, or audit the whole system from its manifest.</p>";
    }
    if (pick) {
      pick.classList.remove("hidden");
      pick.querySelector('[data-pick-options="models"]').classList.toggle("hidden", !totalModels);
      pick.querySelector('[data-pick-options="agents"]').classList.toggle("hidden", !agents.length);
      refreshPick();
    }
  };

  rescanBtn.addEventListener("click", launchScan);

  (async () => {
    const latest = await api("/api/discover/latest").catch(() => null);
    if (latest && latest.runId) {
      sub.textContent = `Reading a scan from ${latest.ageSeconds}s ago…`;
      await loadPicker(latest.runId);
    } else {
      launchScan();
    }
  })();
}

function refreshPick() {
  const pick = document.querySelector("[data-pick]");
  if (!pick) return;
  const models = [...document.querySelectorAll("[data-pick-model]:checked")];
  const agents = [...document.querySelectorAll("[data-pick-agent]:checked")];
  const modelsBtn = pick.querySelector('[data-pick="models"]');
  const agentsBtn = pick.querySelector('[data-pick="agents"]');
  modelsBtn.disabled = models.length === 0;
  modelsBtn.textContent = `Audit ${models.length} model(s)`;
  agentsBtn.disabled = agents.length === 0;
  agentsBtn.textContent = `Test ${agents.length} agent(s)`;
  const anyRerouted = agents.some((c) => JSON.parse(c.dataset.payload).rerouted);
  pick.querySelector("[data-pick-rerouted]").classList.toggle("hidden", !anyRerouted);
}
document.addEventListener("change", (event) => {
  if (event.target.matches("[data-pick-model], [data-pick-agent]")) refreshPick();
});

function modelPickArgs(pick) {
  const f = document.querySelector('[data-pick-options="models"]');
  const suites = [...f.querySelectorAll('input[name="pick-suite"]:checked')].map((c) => c.value);
  const args = ["run", "--model", pick.model, "--backend", pick.backend];
  if (pick.baseUrl) args.push("--base-url", pick.baseUrl);
  const ctx = (f.querySelector('[name="pick-num-ctx"]').value || "").trim();
  if (ctx) args.push("--num-ctx", ctx);
  for (const s of suites.includes("all") || !suites.length ? ["all"] : suites) args.push("--suite", s);
  return args;
}
function agentPickArgs(pick) {
  const f = document.querySelector('[data-pick-options="agents"]');
  const args = ["smith", "run", "--agent", pick.adapter, "--endpoint", pick.endpoint];
  const scenarios = f.querySelector('[name="pick-scenarios"]').value;
  if (scenarios) args.push("--scenarios", scenarios);
  const repeats = (f.querySelector('[name="pick-repeats"]').value || "").trim();
  if (repeats) args.push("-n", repeats);
  return args;
}

document.addEventListener("click", async (event) => {
  const pickBtn = event.target.closest("[data-pick]");
  if (!pickBtn) return;
  const kind = pickBtn.getAttribute("data-pick");
  const pick = document.querySelector("[data-pick]");
  const error = pick.querySelector("[data-pick-error]");
  error.classList.add("hidden");
  if (kind === "system") {
    document.getElementById("launch-system").scrollIntoView({behavior: "smooth"});
    return;
  }
  const selector = kind === "models" ? "[data-pick-model]:checked" : "[data-pick-agent]:checked";
  const picks = [...document.querySelectorAll(selector)].map((c) => JSON.parse(c.dataset.payload));
  if (!picks.length) return;
  const confirmed = kind === "agents" && pick.querySelector("[data-pick-confirm]").checked;
  if (kind === "agents" && picks.some((p) => p.rerouted) && !confirmed) {
    error.textContent = "Confirm that every rerouted agent ticked above points at OPAAS's mock server.";
    error.classList.remove("hidden");
    return;
  }
  const argsList = picks.map(kind === "models" ? modelPickArgs : agentPickArgs);
  pickBtn.disabled = true;
  const ids = [];
  for (const args of argsList) {
    try {
      const job = await api("/api/jobs", {args, ...(kind === "agents" ? {confirmRerouted: confirmed} : {})});
      ids.push(job.id);
    } catch (e) {
      error.textContent = e.message;
      error.classList.remove("hidden");
      break;
    }
  }
  pickBtn.disabled = false;
  if (ids.length) location.href = `/batch?ids=${ids.join(",")}`;
});

// --- Following a run's progress --------------------------------------------
function follow(runId, onLine, onEnd) {
  const source = new EventSource(`/runs/${runId}/events`);
  source.onmessage = (event) => onLine(JSON.parse(event.data));
  source.addEventListener("status", (event) => {
    source.close();
    onEnd(JSON.parse(event.data).status);
  });
  return source;
}
function eventItem(line) {
  const li = document.createElement("li");
  const time = document.createElement("span");
  time.className = "mono dim";
  time.textContent = (line.t || "").slice(11, 19) + " ";
  li.append(time);
  if (line.phase) {
    const b = document.createElement("b");
    b.textContent = line.phase + " ";
    li.append(b);
  }
  li.append(document.createTextNode(line.message || ""));
  return li;
}

// Run page: a run still running gets its live log.
const live = document.querySelector(".live[data-run]");
if (live) {
  const list = live.querySelector("#events");
  follow(live.dataset.run, (line) => list.prepend(eventItem(line)), () => location.reload());
}

// --- Job progress: one bench run's suites, or one audit's 6 steps ----------
// Shared by job.html (one job, full detail) and batch.html (several jobs,
// each a lighter row): both expose the same data-* hooks, trackJob fills in
// whichever are present.

function ensureOverallRow(phases) {
  let row = phases.querySelector(".phase.overall");
  if (!row) {
    row = document.createElement("div");
    row.className = "phase overall";
    row.innerHTML = '<span>Overall</span><div class="pbar"><i></i></div><span class="mono dim"></span>';
    phases.prepend(row);
  }
  return row;
}
function setOverall(phases, percent) {
  if (!phases) return;
  const row = ensureOverallRow(phases);
  row.querySelector("i").style.width = `${Math.min(Math.max(percent, 0), 100)}%`;
  row.lastChild.textContent = `${Math.round(percent)}%`;
}

// One suite at a time fills its own bar to 100%, from the real local percent
// (the raw `percent` field is the *whole run's*, weighted across every
// suite — `index`/`total` turn it back into this suite's own share). A new
// suite starting closes every earlier one, even if its last update rounded
// short of 100.
function phaseBar(phases, bars, line) {
  if (!line.phase || line.percent === undefined) return;
  setOverall(phases, line.percent);
  let row = bars.get(line.phase);
  if (!row) {
    for (const r of bars.values()) {
      r.classList.remove("active");
      r.classList.add("done");
      r.querySelector("i").style.width = "100%";
      r.lastChild.textContent = "100%";
    }
    row = document.createElement("div");
    row.className = "phase active";
    row.innerHTML = '<span></span><div class="pbar"><i></i></div><span class="mono dim"></span>';
    row.firstChild.textContent = line.phase;
    phases.append(row);
    bars.set(line.phase, row);
  }
  const total = line.total || 1;
  const local = line.kind === "suite-end" ? 100
    : Math.max(0, Math.min(100, line.percent * total - ((line.index || 1) - 1) * 100));
  row.querySelector("i").style.width = `${local}%`;
  row.lastChild.textContent = `${Math.round(local)}%`;
  if (line.kind === "suite-end") {
    row.classList.remove("active");
    row.classList.add("done");
  }
}

//: `cli/audit.py`'s step names, in the fixed order `system.orchestrate.STEPS` runs them.
const STEP_LABEL = {discover: "Discover", bench: "Bench", smith: "Smith", sentinel: "Sentinel",
                    inquest: "Inquest", swarm: "Swarm"};

// An audit's 6 steps, from `job.steps` (views.audit_live_steps): each may have
// several invocations (several models under "bench", say). A step with its
// own measured percent (only "bench" ever has one) shows it; the rest show
// running/done/pending, never an invented number.
function renderAuditSteps(phases, rows, steps) {
  ensureOverallRow(phases);
  let done = 0;
  for (const step of steps) {
    let row = rows.get(step.name);
    if (!row) {
      row = document.createElement("div");
      row.innerHTML = '<span></span><div class="pbar"><i></i></div><span class="mono dim"></span>';
      row.firstChild.textContent = STEP_LABEL[step.name] || step.name;
      phases.append(row);
      rows.set(step.name, row);
    }
    const invocations = step.invocations || [];
    const failed = invocations.some((i) => i.status === "failed");
    const running = invocations.some((i) => i.status === "running");
    const state = invocations.length === 0 ? "pending" : running ? "running" : failed ? "failed" : "done";
    if (state === "done") done += 1;
    row.className = `phase step-${state}`;
    const bar = row.querySelector(".pbar");
    const fill = row.querySelector("i");
    const pcts = invocations.filter((i) => i.percent != null).map((i) => i.percent);
    bar.classList.toggle("indeterminate", state === "running" && pcts.length === 0);
    fill.style.width = state === "done" ? "100%"
      : state === "running" && pcts.length ? `${Math.max(...pcts)}%` : "0%";
    row.lastChild.textContent = invocations.length > 1 ? `${state} ×${invocations.length}`
      : state === "running" && pcts.length ? `${Math.round(pcts[0])}%` : state;
  }
  setOverall(phases, steps.length ? (100 * done) / steps.length : 0);
}

function announceReady(ready, mobCanvas, done, status) {
  if (!ready || !done.run) return;
  const files = Object.keys(done.run.files || {});
  const report = done.reportFile;
  const certificate = files.find((f) => f.startsWith("certificate-v") && f.endsWith(".html"));
  const link = `/runs/${done.run.runId}`;
  if (done.run.kind === "compare" && status === "completed") {
    ready.innerHTML = "";
    const a = document.createElement("a");
    a.href = link;
    a.textContent = `Comparison ready · run ${done.run.runId}`;
    ready.append(a);
    ready.classList.remove("hidden");
  } else if (certificate && status === "completed") {
    ready.innerHTML = "";
    const a = document.createElement("a");
    a.href = `/runs/${done.run.sourceRun}`;
    a.textContent = `Certificate v${done.run.version} ready · run ${done.run.sourceRun}`;
    ready.append(a);
    ready.classList.remove("hidden");
  } else if ((status === "completed" || status === "partial") && report) {
    ready.innerHTML = "";
    const a = document.createElement("a");
    a.href = link;
    a.textContent = `Report ready · run ${done.run.runId}`;
    ready.append(a);
    ready.classList.remove("hidden");
    mob(mobCanvas, "mob");
  } else if (status === "failed") {
    mob(mobCanvas, "sentinel");
  } else {
    ready.innerHTML = `<a href="${link}">See run ${done.run.runId}</a>`;
    ready.classList.remove("hidden");
  }
}

// Drives one job's row to completion: `root` is `.job[data-job]` (job.html,
// every hook present) or `[data-job-row]` (batch.html, phases/status/ready
// only). Missing hooks are simply skipped.
function trackJob(id, root) {
  const phases = root.querySelector("[data-phases]");
  const statusBox = root.querySelector("[data-status]");
  const logBox = root.querySelector("[data-log]");
  const eventsBox = root.querySelector("[data-events]");
  const cancelBtn = root.querySelector("[data-cancel]");
  const readyBox = root.querySelector("[data-ready]");
  const mobCanvas = root.querySelector("[data-mob]");
  const bars = new Map();
  const stepRows = new Map();
  let following = false;
  let first = true;
  let audit = false;

  const setState = (status) => {
    if (cancelBtn) cancelBtn.classList.toggle("hidden", !["queued", "running"].includes(status));
    if (!statusBox) return;
    statusBox.innerHTML = "";
    const span = document.createElement("span");
    span.className = `state s-${status}`;
    span.textContent = status;
    statusBox.append(span);
  };

  if (cancelBtn) {
    cancelBtn.addEventListener("click", async () => {
      await api(`/api/jobs/${id}/cancel`, {}).catch((e) => alert(e.message));
    });
  }

  const poll = async () => {
    const job = await api(`/api/jobs/${id}`).catch(() => null);
    if (!job) return setTimeout(poll, 1500);
    if (logBox) logBox.textContent = job.log || "";
    if (job.run && job.run.kind === "audit") {
      audit = true;
      if (phases) renderAuditSteps(phases, stepRows, job.steps || []);
    }
    if (!following) setState(job.run ? job.run.status : job.status);
    if (job.run && !following) {
      following = true;
      follow(job.run.runId, (line) => {
        if (eventsBox) {
          if (first) { eventsBox.innerHTML = ""; first = false; }
          eventsBox.prepend(eventItem(line));
        }
        if (!audit && phases) phaseBar(phases, bars, line);
      }, async (status) => {
        setState(status);
        const done = await api(`/api/jobs/${id}`).catch(() => job);
        if (logBox) logBox.textContent = done.log || "";
        if (phases) {
          if (done.run && done.run.kind === "audit") renderAuditSteps(phases, stepRows, done.steps || []);
          else setOverall(phases, 100);
        }
        announceReady(readyBox, mobCanvas, done, status);
      });
    }
    if (["queued", "running"].includes(job.status) || (!job.run && job.status === "finished")) {
      setTimeout(poll, audit ? 1500 : 1000);
    } else if (!job.run) {
      setState(job.status);
    }
  };
  poll();
}

const jobBox = document.querySelector(".job[data-job]");
if (jobBox) trackJob(jobBox.dataset.job, jobBox);
for (const row of document.querySelectorAll("[data-job-row]")) trackJob(row.dataset.jobRow, row);

// --- The meerkat mob (docs/dashboard.md §7, *The meerkat*) ------------------
// Drawn from the CLI's own grid (/mascot.json): never redrawn by hand. Once,
// about four seconds, no sound. With reduce motion, shown already standing. A
// failed run brings out only the sentinel.
async function mob(canvas, mode) {
  if (!canvas) return;
  const sprite = await api("/mascot.json").catch(() => null);
  if (!sprite) return;
  canvas.classList.remove("hidden");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, ground = H - 26;
  const rows = sprite.rows, palette = sprite.palette;
  const draw = (x, y, s, flip) => {
    for (let r = 0; r < rows.length; r++) {
      const line = rows[r];
      for (let k = 0; k < line.length; k++) {
        const ch = line[k];
        if (ch === ".") continue;
        ctx.fillStyle = palette[ch];
        const cx = flip ? x + (line.length - 1 - k) * s : x + k * s;
        ctx.fillRect(Math.floor(cx), Math.floor(y + r * s), Math.ceil(s), Math.ceil(s));
      }
    }
  };
  const all = [
    {x: 60, s: 2.2, flip: false, d: 0}, {x: 190, s: 1.8, flip: true, d: 260},
    {x: 300, s: 2.6, flip: false, d: 120, lead: true}, {x: 440, s: 1.4, flip: true, d: 520, pup: true},
    {x: 515, s: 1.9, flip: false, d: 380},
  ];
  const meerkats = mode === "sentinel" ? [{...all[2], d: 0}] : all;
  // Reduced motion, or a tab in the background (where the browser throttles
  // frames and the mob would freeze half out): shown standing, at once.
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden;
  const ease = (t) => { t = Math.min(Math.max(t, 0), 1); const c = 1.4; return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2; };
  let start = 0;
  const frame = (now) => {
    if (!start) start = now;
    const t = still ? 1e9 : now - start;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#c38851"; ctx.fillRect(0, ground, W, H - ground);
    ctx.fillStyle = "#ab7045"; ctx.fillRect(0, ground, W, 3);
    for (const m of meerkats) {
      const cx = m.x + 40 * m.s * 0.45;
      ctx.fillStyle = "#392418";
      ctx.beginPath(); ctx.ellipse(cx, ground + 3, 40 * m.s * 0.38, 4, 0, 0, Math.PI * 2); ctx.fill();
    }
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, ground + 3); ctx.clip();
    for (const m of meerkats) {
      const h = rows.length * m.s;
      let y = ground - h * ease((t - m.d) / 650) + 3;
      let flip = m.flip;
      if (m.lead && t > 2300 && t < 3300) flip = !flip;
      if (m.pup && t > 1600 && t < 4000) y += Math.round(Math.sin((t - 1600) / 140) * 2);
      draw(m.x, y, m.s, flip);
    }
    ctx.restore();
    if (t < 4400) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// --- Sentinel: follow the proxy's log and the gate's queue ------------------
// The paths are the operator's; the browser remembers them for next time.
const SENTINEL_KEY = "opaas.sentinel";
const sentinelForm = document.getElementById("sentinel-paths");
if (sentinelForm) {
  const params = new URLSearchParams(location.search);
  if (!params.get("log") && !params.get("queue")) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SENTINEL_KEY) || "null"); } catch (e) { saved = null; }
    if (saved && (saved.log || saved.queue)) {
      location.replace(`/sentinel?${new URLSearchParams(saved)}`);
    }
  } else {
    try {
      localStorage.setItem(SENTINEL_KEY, JSON.stringify(
        {log: params.get("log") || "", queue: params.get("queue") || ""}));
    } catch (e) { /* private window: nothing is remembered */ }
  }
}

const logBox = document.querySelector("[data-sentinel-log]");
if (logBox) {
  const body = logBox.querySelector("[data-events]");
  const log = logBox.getAttribute("data-sentinel-log");
  const source = new EventSource(`/sentinel/events?${new URLSearchParams({log})}`);
  source.onmessage = (message) => {
    const e = JSON.parse(message.data);
    const row = document.createElement("tr");
    const cell = (text, cls) => {
      const td = document.createElement("td");
      if (cls) td.className = cls;
      td.textContent = text;
      row.append(td);
      return td;
    };
    cell((e.ts || "").slice(11, 19), "mono dim");
    cell(e.actors || "—");
    const action = cell("");
    const b = document.createElement("b");
    b.textContent = e.action + " ";
    action.append(b, document.createTextNode(e.target || ""));
    const verdicts = cell("");
    for (const v of e.verdicts) {
      const span = document.createElement("span");
      span.className = `verdict v-${v.verdict}`;
      span.textContent = `${v.policy}: ${v.verdict}`;
      verdicts.append(span, document.createTextNode(" "));
    }
    if (!e.verdicts.length) verdicts.innerHTML = '<span class="dim">no policy</span>';
    cell(e.durationMs ?? "", "r mono");
    const empty = body.querySelector("td[colspan]");
    if (empty) empty.parentElement.remove();
    body.prepend(row);
  };
}

const queueBox = document.querySelector("[data-sentinel-queue]");
if (queueBox) {
  const count = queueBox.querySelector("[data-pending-count]");
  const queue = queueBox.getAttribute("data-sentinel-queue");
  setInterval(async () => {
    const pending = await api(`/api/sentinel/pending?${new URLSearchParams({queue})}`)
      .catch(() => null);
    if (pending && String(pending.length) !== count.textContent) location.reload();
  }, 3000);
}

// --- History: compare the ticked runs of one kind with `opaas compare` -------
const compareBar = document.querySelector("[data-compare-bar]");
if (compareBar) {
  const button = compareBar.querySelector("[data-compare]");
  const error = compareBar.querySelector("[data-compare-error]");
  const ticked = () => [...document.querySelectorAll("[data-compare-pick]:checked")];
  const picked = () => ticked().map((c) => c.value);
  document.addEventListener("change", (event) => {
    if (!event.target.matches("[data-compare-pick]")) return;
    // One kind per comparison: `opaas compare` refuses a model against an agent,
    // and the page says so before launching rather than after.
    const kinds = new Set(ticked().map((c) => c.dataset.compareKind));
    const count = picked().length;
    button.classList.toggle("hidden", count < 2 || kinds.size > 1);
    button.textContent = `Compare ${count} runs`;
    error.textContent = kinds.size > 1
      ? "Tick runs of one kind: models, agents and systems are not measured on the same terms." : "";
    error.classList.toggle("hidden", kinds.size <= 1);
  });
  button.addEventListener("click", async () => {
    const args = ["compare"];
    for (const path of picked()) args.push("--report", path);
    try { await launch(args); }
    catch (e) { error.textContent = e.message; error.classList.remove("hidden"); }
  });
}
