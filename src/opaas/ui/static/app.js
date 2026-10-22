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

// --- New audit: scan this machine, then audit the whole system --------------
// The scan is a real, recorded `discover scan` job, never a probe from the
// server itself. While it runs the sentinel keeps watch. What it found is
// listed, and the system's manifest is drafted from that very inventory.
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Polls a job until it ends: its last state, with the run it produced if any.
async function settled(id, isDone) {
  for (;;) {
    const job = await api(`/api/jobs/${id}`).catch(() => null);
    if (job && (isDone(job) || !["queued", "running"].includes(job.status))) return job;
    await delay(1000);
  }
}

// Shown as a shell would need it, so the preview can be pasted as it is.
const quoted = (word) => (/[\s"'$`\\]/.test(word) ? `"${word.replace(/(["$`\\])/g, "\\$1")}"` : word);

const systemForm = document.getElementById("launch-system");
let inventoryPath = null;

function systemCommands(form) {
  const f = new FormData(form);
  const value = (name) => (f.get(name) || "").trim();
  const manifest = value("manifest");
  const commands = [];
  if (f.get("draft")) {
    const draft = ["audit", "draft", "-o", manifest];
    if (inventoryPath) draft.push("--inventory", inventoryPath);
    if (value("purpose")) draft.push("--purpose", value("purpose"));
    commands.push(draft);
  }
  commands.push(["audit", "run", manifest]);
  return commands;
}

if (systemForm) {
  const preview = systemForm.querySelector("[data-preview]");
  const error = systemForm.querySelector("[data-error]");
  const submit = systemForm.querySelector('button[type="submit"]');
  const purpose = systemForm.querySelector('[name="purpose"]').closest("label");
  const fail = (message) => { error.textContent = message; error.classList.remove("hidden"); };
  const refresh = () => {
    preview.textContent = systemCommands(systemForm)
      .map((args) => "$ opaas " + args.map(quoted).join(" ")).join("\n");
    purpose.classList.toggle("hidden", !systemForm.querySelector('[name="draft"]').checked);
  };
  systemForm.addEventListener("input", (event) => {
    if (event.target.name === "manifest") event.target.dataset.edited = "1";
    refresh();
  });
  systemForm.addEventListener("refresh", refresh);
  systemForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const manifest = new FormData(systemForm).get("manifest").trim();
    if (!manifest) return fail("Enter the path of the system manifest.");
    // Each job runs in its own directory: a relative path would name two different files.
    if (!/^([A-Za-z]:[\\/]|[\\/])/.test(manifest)) {
      return fail("Enter the manifest's full path, e.g. C:\\audits\\system.json.");
    }
    const commands = systemCommands(systemForm);
    submit.disabled = true;
    try {
      // The draft must exist before the audit reads it: one job, then the other.
      if (commands.length === 2) {
        submit.textContent = "Drafting the manifest…";
        const draft = await api("/api/jobs", {args: commands[0]});
        const done = await settled(draft.id, () => false);
        if (done.exitCode !== 0) {
          const said = (done.log || "").trim().split("\n").filter((l) => l.trim()).slice(-3).join(" ");
          throw new Error(said || "The manifest could not be drafted.");
        }
      }
      await launch(commands[commands.length - 1]);
    } catch (e) {
      fail(e.message);
      submit.disabled = false;
      submit.textContent = "Audit the whole system";
    }
  });
  refresh();
}

const discoverBox = document.querySelector("[data-discover]");
if (discoverBox) {
  const body = discoverBox.querySelector("[data-discover-body]");
  const sub = discoverBox.querySelector("[data-discover-sub]");
  const scanBtn = discoverBox.querySelector("[data-scan]");
  const canvas = discoverBox.querySelector("[data-lookout]");

  const card = (title, items) => {
    const div = document.createElement("div");
    div.className = "card";
    const h3 = document.createElement("h3");
    h3.textContent = title;
    const ul = document.createElement("ul");
    ul.className = "found";
    for (const [text, detail] of items) {
      const li = document.createElement("li");
      li.textContent = text + " ";
      if (detail) {
        const small = document.createElement("span");
        small.className = "dim mono";
        small.textContent = detail;
        li.append(small);
      }
      ul.append(li);
    }
    if (!items.length) ul.innerHTML = '<li class="dim">No models reported.</li>';
    div.append(h3, ul);
    return div;
  };

  // --- Scope: one environment's folder, or the whole machine (D85) ---------
  const scopeBox = discoverBox.querySelector("[data-scope]");
  const folderInput = scopeBox.querySelector('[name="project"]');
  const scanPreview = discoverBox.querySelector("[data-scan-preview]");
  const scoped = () => scopeBox.querySelector('[name="scope"]:checked').value === "project";
  const scanArgs = () => (scoped()
    ? ["discover", "scan", "--project", folderInput.value.trim()]
    : ["discover", "scan", "--well-known"]);
  const refreshScope = () => {
    discoverBox.querySelector("[data-folder]").classList.toggle("hidden", !scoped());
    discoverBox.querySelector("[data-folder-note]").classList.toggle("hidden", !scoped());
    if (!scoped()) browser.classList.add("hidden");
    scanPreview.textContent = "$ opaas " + scanArgs().map(quoted).join(" ");
  };

  // The folder picker lists folders only, one level at a time, from the server:
  // a browser page cannot hand a local path to anyone.
  const browser = discoverBox.querySelector("[data-browser]");
  const browserList = browser.querySelector("[data-browser-list]");
  const browserPath = browser.querySelector("[data-browser-path]");
  let here = null;
  const join = (folder, name) =>
    `${folder}${folder.includes("\\") ? "\\" : "/"}${name}`.replace(/([\/])[\/]+/g, "$1");
  const same = (a, b) => a.replace(/[\/]+$/, "").toLowerCase() === b.replace(/[\/]+$/, "").toLowerCase();
  let picked = null;
  // One click picks a folder; "Open" (or a double click) goes into it.
  const browse = async (path, select) => {
    const listing = await api(`/api/folders?${new URLSearchParams({path})}`).catch(() => null);
    if (!listing) return;
    here = listing;
    picked = null;
    browser.classList.remove("hidden");
    browserPath.textContent = listing.path;
    browser.querySelector("[data-up]").disabled = !listing.parent;
    browserList.innerHTML = "";
    for (const name of listing.folders) {
      const path = join(listing.path, name);
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = name;
      const open = document.createElement("button");
      open.type = "button";
      open.className = "btn small";
      open.textContent = "Open";
      li.append(label, open);
      const pick = () => {
        for (const row of browserList.children) row.classList.remove("on");
        li.classList.add("on");
        picked = path;
        folderInput.value = path;
        refreshScope();
      };
      li.addEventListener("click", (event) => (event.target === open ? browse(path) : pick()));
      li.addEventListener("dblclick", () => browse(path));
      browserList.append(li);
      if (select && same(path, select)) { pick(); li.scrollIntoView({block: "nearest"}); }
    }
    if (!listing.folders.length) browserList.innerHTML = '<li class="dim">No folders here.</li>';
  };
  // Opens beside the folder already named, with it picked, so a sibling is one click away.
  discoverBox.querySelector("[data-browse]").addEventListener("click", async () => {
    const current = folderInput.value.trim();
    const listing = await api(`/api/folders?${new URLSearchParams({path: current})}`).catch(() => null);
    if (listing && listing.parent) browse(listing.parent, listing.path);
    else browse(current);
  });
  browser.querySelector("[data-up]").addEventListener("click", () => here && here.parent && browse(here.parent, here.path));
  browser.querySelector("[data-choose]").addEventListener("click", () => {
    if (!picked && here) folderInput.value = here.path;
    browser.classList.add("hidden");
    refreshScope();
  });
  scopeBox.addEventListener("input", refreshScope);
  refreshScope();

  const host = (data) => {
    const box = document.createElement("div");
    for (const engine of data.engines || []) {
      box.append(card(`${engine.kind} · :${engine.port} · ${engine.version || "?"} · ${engine.bind}`,
        (engine.models || []).map((m) => [m.name, m.quantization])));
    }
    if ((data.agents || []).length) {
      box.append(card("Agents", data.agents.map((a) => [`${a.name || a.kind} · ${a.adapter}`, a.endpoint])));
    }
    return box;
  };

  const show = (data) => {
    const engines = data.engines || [];
    const agents = data.agents || [];
    const models = engines.reduce((n, e) => n + (e.models || []).length, 0);
    const project = data.project;
    body.innerHTML = "";
    if (project) {
      sub.textContent = `Environment ${project.root}: ${project.agents.length} agent(s), `
        + `${project.models.length} model(s) declared, from ${project.files.length} file(s).`;
      body.append(card(`Agents of ${project.root}`, project.agents.map((a) =>
        [`${a.name} · ${a.kind}`, `${a.file} · ${a.running || "not running"}`])));
      body.append(card(`Models of ${project.root}`, project.models.map((m) =>
        [m.name, m.servedBy || "served by no engine here"])));
      if (!project.models.length) {
        body.lastChild.querySelector("ul").innerHTML =
          '<li class="dim">None named in the files OPAAS reads; a model named only in code is not seen.</li>';
      }
      if (!project.agents.length) {
        body.firstChild.querySelector("ul").innerHTML =
          '<li class="dim">No langgraph.json, agent card, or n8n / Flowise export with an agent.</li>';
      }
      const rest = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `Also on this machine, not part of this environment: ${models} model(s), ${agents.length} agent(s)`;
      rest.append(summary, host(data));
      body.append(rest);
    } else {
      sub.textContent = engines.length || agents.length
        ? `This machine: ${engines.length} engine(s), ${models} model(s), ${agents.length} agent(s) found.`
        : "Nothing found running on this machine.";
      body.append(host(data));
      if (!agents.length) {
        const p = document.createElement("p");
        p.className = "fine";
        p.textContent = "Agents are found on the whole machine only when they run and identify "
          + "themselves. Scan an environment's folder to find the agents it declares.";
        body.append(p);
      }
    }
    inventoryPath = data.inventoryPath || null;
    if (systemForm) {
      // The manifest belongs with the environment it describes, unless named already.
      const manifest = systemForm.querySelector('[name="manifest"]');
      if (project && !manifest.dataset.edited && scoped()) {
        const folder = folderInput.value.trim().replace(/[\\/]+$/, "");
        manifest.value = folder + (folder.includes("\\") ? "\\" : "/") + "system.json";
      }
      systemForm.classList.remove("hidden");
      systemForm.dispatchEvent(new Event("refresh"));
    }
  };

  const scan = async () => {
    if (scoped() && !folderInput.value.trim()) {
      sub.textContent = "Name the environment's folder, or choose the whole machine.";
      return;
    }
    scanBtn.disabled = true;
    sub.textContent = scoped() ? "Scanning the environment and the engines that serve it…"
      : "Scanning this machine for engines, models and agents…";
    body.innerHTML = "";
    const stop = lookout(canvas);
    let data = null;
    try {
      const job = await api("/api/jobs", {args: scanArgs()});
      // At least long enough to see the sentinel look both ways.
      const [done] = await Promise.all([
        settled(job.id, (j) => j.run && j.run.status !== "running"), delay(2500)]);
      if (done.run) data = await api(`/api/discover/${done.run.runId}`).catch(() => null);
      if (!data) sub.textContent = "The scan did not complete. Its output is on the Runs page; scan again.";
    } catch (e) {
      sub.textContent = `Could not start the scan: ${e.message}`;
    }
    stop();
    if (data) show(data);
    scanBtn.disabled = false;
    scanBtn.textContent = "Scan again";
  };
  scanBtn.addEventListener("click", scan);

  // A scan from the last few minutes of the very scope on screen (the folder
  // `opaas ui` started in, by default) is shown, not rerun. Any other scan is
  // not this environment's: the page waits for Scan.
  (async () => {
    const latest = await api("/api/discover/latest").catch(() => null);
    if (!latest || !latest.runId) return;
    if (scoped() ? !(latest.project && same(latest.project, folderInput.value.trim())) : latest.project) return;
    const data = await api(`/api/discover/${latest.runId}`).catch(() => null);
    if (!data) return;
    show(data);
    sub.textContent += ` Scanned ${latest.ageSeconds}s ago.`;
    scanBtn.textContent = "Scan again";
  })();
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

// --- The meerkats (docs/dashboard.md §7, *The meerkat*) --------------------
// Drawn from the CLI's own grid (/mascot.json): never redrawn by hand, no sound.
// With reduce motion, or in a background tab (where the browser throttles
// frames and a meerkat would freeze half out), shown already standing.
const ease = (t) => { t = Math.min(Math.max(t, 0), 1); const c = 1.4; return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2; };
const stillMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden;

// The ground, a burrow under each meerkat, then each one clipped at ground
// level so it rises out of its burrow: `place(m)` says where it is now.
function scene(canvas, sprite, meerkats, place) {
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
    const {rise, flip, dy} = place(m);
    draw(m.x, ground - rows.length * m.s * rise + 3 + (dy || 0), m.s, flip);
  }
  ctx.restore();
}

// The mob, when a report is generated: once, about four seconds. A failed run
// brings out only the sentinel.
async function mob(canvas, mode) {
  if (!canvas) return;
  const sprite = await api("/mascot.json").catch(() => null);
  if (!sprite) return;
  canvas.classList.remove("hidden");
  const all = [
    {x: 60, s: 2.2, flip: false, d: 0}, {x: 190, s: 1.8, flip: true, d: 260},
    {x: 300, s: 2.6, flip: false, d: 120, lead: true}, {x: 440, s: 1.4, flip: true, d: 520, pup: true},
    {x: 515, s: 1.9, flip: false, d: 380},
  ];
  const meerkats = mode === "sentinel" ? [{...all[2], d: 0}] : all;
  const still = stillMotion();
  let start = 0;
  const frame = (now) => {
    if (!start) start = now;
    const t = still ? 1e9 : now - start;
    scene(canvas, sprite, meerkats, (m) => ({
      rise: ease((t - m.d) / 650),
      flip: m.lead && t > 2300 && t < 3300 ? !m.flip : m.flip,
      dy: m.pup && t > 1600 && t < 4000 ? Math.round(Math.sin((t - 1600) / 140) * 2) : 0,
    }));
    if (t < 4400) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// The sentinel on watch while the machine is scanned: it rises, then looks one
// way and the other until `stop()`, which puts it away. The only meerkat that
// loops, because it stands for work still going on, and ends with it.
function lookout(canvas) {
  let watching = true;
  (async () => {
    const sprite = await api("/mascot.json").catch(() => null);
    if (!sprite || !watching) return;
    canvas.classList.remove("hidden");
    const s = 2;
    const sentinel = [{x: (canvas.width - 40 * s) / 2, s}];
    const still = stillMotion();
    let start = 0;
    const frame = (now) => {
      if (!watching) return;
      if (!start) start = now;
      const t = still ? 1e9 : now - start;
      scene(canvas, sprite, sentinel, () => ({
        rise: ease(t / 650),
        flip: !still && t > 900 && Math.floor((t - 900) / 1100) % 2 === 1,
      }));
      if (!still) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  })();
  return () => { watching = false; canvas.classList.add("hidden"); };
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
