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
function discoverArgs(form) {
  const args = ["discover", "scan"];
  if (new FormData(form).get("well_known")) args.push("--well-known");
  return args;
}
for (const form of document.querySelectorAll("form.launch")) {
  // getAttribute, not form.dataset: a control named "dataset" shadows it on a form.
  const kind = form.getAttribute("data-kind");
  const build = {bench: benchArgs, agent: agentArgs, discover: discoverArgs}[kind];
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
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const args = build(form);
    if (kind === "bench" && !args[2]) {
      error.textContent = "Enter the model id exactly as the backend serves it.";
      error.classList.remove("hidden");
      return;
    }
    const problem = kind === "agent" ? agentProblem(form) : null;
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

// Job page: wait for the job's run, follow it, and greet a report with the mob.
const jobBox = document.querySelector(".job[data-job]");
if (jobBox) {
  const id = jobBox.dataset.job;
  const events = jobBox.querySelector("[data-events]");
  const phases = jobBox.querySelector("[data-phases]");
  const statusBox = jobBox.querySelector("[data-status]");
  const log = jobBox.querySelector("[data-log]");
  const bars = new Map();
  let following = false;
  let first = true;

  const setState = (status) => {
    // Only a queued or running job can be cancelled.
    jobBox.querySelector("[data-cancel]").classList.toggle(
      "hidden", !["queued", "running"].includes(status));
    statusBox.innerHTML = "";
    const span = document.createElement("span");
    span.className = `state s-${status}`;
    span.textContent = status;
    statusBox.append(span);
  };
  const phaseBar = (line) => {
    if (!line.phase || line.percent === undefined) return;
    let row = bars.get(line.phase);
    if (!row) {
      row = document.createElement("div");
      row.className = "phase";
      row.innerHTML = '<span></span><div class="pbar"><i></i></div><span class="mono dim"></span>';
      row.firstChild.textContent = line.phase;
      phases.append(row);
      bars.set(line.phase, row);
    }
    row.querySelector("i").style.width = `${Math.min(line.percent, 100)}%`;
    row.lastChild.textContent = `${Math.round(line.percent)}%`;
  };

  jobBox.querySelector("[data-cancel]").addEventListener("click", async () => {
    await api(`/api/jobs/${id}/cancel`, {}).catch((e) => alert(e.message));
  });

  const poll = async () => {
    const job = await api(`/api/jobs/${id}`).catch(() => null);
    if (!job) return setTimeout(poll, 1500);
    log.textContent = job.log || "";
    if (!following) setState(job.run ? job.run.status : job.status);
    if (job.run && !following) {
      following = true;
      follow(job.run.runId, (line) => {
        if (first) { events.innerHTML = ""; first = false; }
        events.prepend(eventItem(line));
        phaseBar(line);
      }, async (status) => {
        setState(status);
        const done = await api(`/api/jobs/${id}`).catch(() => job);
        log.textContent = done.log || "";
        const files = Object.keys((done.run && done.run.files) || {});
        // The server says which file is the run's report: an agent's is named freely.
        const report = done.reportFile;
        const draft = files.find((f) => f.startsWith("certificate-v") && f.endsWith(".html"));
        const ready = jobBox.querySelector("[data-ready]");
        const link = `/runs/${job.run.runId}`;
        if (draft && status === "completed") {
          // A draft is not a report: no mob, a link to the run it was drawn from.
          ready.innerHTML = "";
          const a = document.createElement("a");
          a.href = `/runs/${done.run.sourceRun}`;
          a.textContent = `Draft certificate v${done.run.version} ready · run ${done.run.sourceRun}`;
          ready.append(a);
          ready.classList.remove("hidden");
        } else if ((status === "completed" || status === "partial") && report) {
          ready.innerHTML = "";
          const a = document.createElement("a");
          a.href = link;
          a.textContent = `Report ready · run ${job.run.runId}`;
          ready.append(a);
          ready.classList.remove("hidden");
          mob(jobBox.querySelector("[data-mob]"), "mob");
        } else if (status === "failed") {
          mob(jobBox.querySelector("[data-mob]"), "sentinel");
        } else {
          ready.innerHTML = `<a href="${link}">See run ${job.run.runId}</a>`;
          ready.classList.remove("hidden");
        }
      });
    }
    if (["queued", "running"].includes(job.status) || (!job.run && job.status === "finished")) {
      setTimeout(poll, 1000);
    } else if (!job.run) {
      setState(job.status);
    }
  };
  poll();
}

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
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
