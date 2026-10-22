# Local dashboard: `opaas ui`

**Status:** proposed (D75). Nothing here is implemented yet. It is built by phases 18–24 of the [work plan](workplan.md#releases-031-and-040) and ships in 0.4.0.

Someone who installs OPAAS today learns it from the README. The cover-page menu (`cli/menu.py`) shows what is installed and prints the next command to copy, but it does not guide. Results land wherever the command was run, and a second run can silently overwrite the first.

This proposal adds a **local dashboard**. It guides a newcomer from discovery to an unsigned certificate, shows every long command's progress live, keeps every run in one history, and compares runs. It does this without becoming a second place where measuring happens.

---

## 1. What does not change

Every invariant in `CLAUDE.md` holds, and the dashboard is one more output they apply to:

- **No aggregate score, no tiers** (1). There is no total, no ranking, no "best run", no colour scale across categories. Each figure is shown per category with its interval.
- **Not measured is declared, never zero** (2). An unmeasured figure is shown as *not measured* with its reason. It is never an empty bar, a zero, or an omission.
- **The certified hash is the hash of the file's bytes** (4). The dashboard shows reports and never rewrites them. Hashes it displays are computed from the files.
- **A difference whose intervals overlap is not a difference** (11). Comparison marks it as such, and it is the same computation as `opaas compare`.
- **The report is a public document** (12), and **evidence is minimised** (N4). The dashboard shows what the files hold, which is digests unless the operator kept a value. It never asks an engine or an agent for more.
- **Thresholds are the operator's** (N6). The dashboard shows the thresholds a system report carries. It sets none and suggests none.

And from the decisions taken for 0.4.0 (work plan, *Releases 0.3.1 and 0.4.0*):

- **The dashboard never signs.** No key, no passphrase and no token PIN passes through it. Signing stays `opaas sign`, in a terminal.
- **Nothing is published.** The audit certificate carries no QR code unless the user names a base URL (D72, D81).

---

## 2. The rule that shapes everything: the server measures nothing

The dashboard server does two things only:

- it **reads** the run store and the files commands write while they run;
- it **launches** `opaas` commands as child processes.

It never imports a suite, an adapter or a scenario runner to measure something itself. A job started from the dashboard is the exact command line the dashboard displays, and the run it produces records that command line. So:

- every result in the history can be reproduced from a terminal, with the command shown next to it;
- the CLI stays the only place where measuring happens, and its tests keep covering every measurement;
- killing the server kills nothing it did not start, and loses nothing. The truth is on disk.

---

## 3. Architecture

```
browser ──HTTP + server-sent events──► opaas ui   (127.0.0.1 only, session token)
                                        ├─ store      reads ~/.opaas/runs, never writes a run
                                        ├─ jobs       launches `opaas …` as a child process, one queue per endpoint
                                        ├─ watcher    follows progress.jsonl
                                        └─ views      server-rendered HTML; the same data as JSON under /api
CLI: opaas run | smith run | audit run | discover | inquest | swarm | compare | certificate --run
        └──writes once──► ~/.opaas/runs/<run-id>/
```

**Stack.** Starlette and uvicorn serve the pages, and Jinja2 renders them. All three ship in the optional `ui` extra, pinned in `uv.lock` and imported only inside `opaas ui`. Without the extra, `opaas ui` says what to install. The base install does not grow.

**Front without a build step.** The pages are rendered on the server.

- **htmx** handles forms and partial updates.
- Its **server-sent-events** extension draws live progress.
- Charts are **SVG generated on the server**: interval bars and progress bars, nothing that needs a charting library.
- The two small scripts are **vendored in the package**, with their hashes recorded. There is no CDN, so the dashboard works air-gapped.
- There is no Node toolchain to install, audit or pin.

**No database.** The run directories are the state. An in-memory index makes listing fast, and it is rebuilt from disk at start. A server restart finds every run again. A job that was running when the server died is shown as *interrupted*, from its `run.json` (§4).

---

## 4. The contract: what commands write, what the dashboard reads

These files are the interface between the CLI and the dashboard. Each carries a `format` number. A reader refuses a format it does not know instead of guessing.

**`~/.opaas/runs/<run-id>/run.json`** is written when the run starts and completed when it ends. It is the only file of a run written twice: once to open it, once to close it, never after.

```json
{
  "format": 1,
  "runId": "20261019-003",
  "kind": "bench",
  "opaasVersion": "0.4.0",
  "specVersion": "1.0-draft",
  "command": ["opaas", "run", "--model", "qwen2.5:7b-instruct", "--backend", "ollama", "--num-ctx", "8192", "--suite", "all"],
  "endpoint": "http://127.0.0.1:11434",
  "startedAt": "2026-10-19T14:00:02Z",
  "endedAt": "2026-10-19T14:41:37Z",
  "status": "completed",
  "files": {"report.json": "sha256:9f3c…a1"}
}
```

- `kind` takes one of these values: `bench`, `smith`, `audit`, `discover`, `inquest`, `swarm`, `sentinel`, `card`, `compare`, `certificate`.
- `status` takes one of these values: `running`, `completed`, `partial`, `failed`, `interrupted`.
  - `interrupted` is what a reader concludes from a `running` run whose process no longer exists. It is never written by the run itself.
- `command` has secrets replaced the way `_publishable_config` replaces them (invariant 12).
- `endpoint` is present for commands that talk to an engine or an agent, and absent otherwise. A field the producer did not have is absent, never defaulted.
- `pid` is present only while the run is `running`. It is how a reader tells a run in progress from one whose process is gone. Closing the run removes it.
- An imported run (`opaas runs import`) has no `command`. It carries `importedFrom` and, when the old report had one, `originalRunId`.

**`progress.jsonl`** has one line per event, appended while the run goes.

```json
{"t": "2026-10-19T14:03:11Z", "kind": "progress", "phase": "capability", "index": 2, "total": 5, "percent": 31.4, "message": "IFEval 160/541"}
```

- It is `RunEvent` (`bench/runner.py`) written to a file, plus the equivalent events of `smith run`, `audit run`, `discover`, `inquest` and `swarm`. Today only `bench` emits progress, so those commands gain the hooks.
- It is an **operational log, not evidence.** It is not hash-chained, not signed and not cited by any report.
- Under N4 a line never carries a prompt, a response, a tool argument, a URL query string or an environment value. A test checks every emitter against that rule.

**`certificate-v<N>.html` and `certificate-v<N>.json`** are written by `opaas certificate --run <run-id>`, without a credential (D77).

- The source run is closed and read-only, so each draft is a run of its own: `kind: certificate`, with `sourceRun` and `version` in its `run.json`.
- N is one more than the drafts of that run already in the store. An earlier version is never touched.
- The report is checked against the hash its run recorded before anything is rendered.
- The JSON records:
  - the report hash;
  - the OPAAS version;
  - the template hash;
  - the generation time.
- Every version is the audit certificate of the run, checked by its report's hash and carrying no signature (D81; earlier versions were marked as drafts, D72).

**Sentinel and the gate** keep their own files: the hash-chained event log, and the approval queue directory (D50). The dashboard does not read them: they are followed from the terminal that runs the proxy (D84).

---

## 5. Jobs

**Launching.** Every form in the dashboard ends on a confirmation that shows the command line it is about to run. The job is that command, started as a child process with the user's environment, in its own working directory `<store>/.jobs/<job-id>/`. Its stdout and stderr go to `job.log` there, so a failure can be read after the fact; the run directory itself is written only by the command (D76). The job's id reaches the run as `jobId` in `run.json` (D79).

**One queue per endpoint.** Two measurements against the same engine at the same time contaminate each other: block A's latency and throughput, and the concurrency OPAAS believes it is sending. So jobs that name the same endpoint run one after another, in the order they were queued. Jobs on different endpoints, and jobs that touch no endpoint (`discover`, `inquest`, `compare`, `certificate`), run alongside. The queue is shown, and a queued job can be removed before it starts.

**A batch is several independent jobs, not one.** `/batch?ids=…` tracks several jobs side by side; nothing in the store knows they were launched together (D83). `/new` no longer queues one (D84).

**Cancelling.** A running job can be cancelled. The process receives an interrupt, and its run ends `interrupted`, keeping whatever it wrote. Nothing is deleted.

**What the dashboard does not launch:**

- **The sentinel proxy.** It sits on the data path of a live system (D46). Starting it, stopping it, changing its policies and following its traffic stay in a terminal (D84).
- **Approvals.** An approval is an attributable event made by a named approver (D50). It is made with `opaas sentinel approve`, in a terminal.
- **Anything with a key:** `sign`, `keygen`, `delegate`, `revoke`.
- **Rerouted agents, without a blocking warning.** Before any rerouted-agent run, the user must confirm that the agent's tools point at OPAAS's mock MCP server. Otherwise its scenarios would act on real tools (D68).

---

## 6. Security on localhost

A server on `127.0.0.1` is still reachable by every local user and by any web page the user opens, through the browser. So:

- **Loopback only.** The server binds `127.0.0.1` and refuses any other address. Remote use goes through an SSH tunnel, and the README shows the command.
- **A session token.** A random token is printed in the start URL. The first request exchanges it for an `HttpOnly`, `SameSite=Strict` cookie. A request with neither is refused.
- **`Host` must be `127.0.0.1:<port>` or `localhost:<port>`**, against DNS rebinding.
- **Every POST carries the token as well.** There is no CORS, and no GET request changes anything.
- **The token lives only in memory.** A new server start means a new token.

---

## 7. Screens

| Screen | Route | Shows |
|---|---|---|
| Overview | `/` | What OPAAS does; what it measures, read from the registries that measure it (blocks A–E from the suites, the auditability dimensions and their metrics from `core.metrics`) and the steps an audit runs (`system.orchestrate.STEPS`) (D84); the latest runs, the queue. What this machine has cached is `opaas`'s menu in the terminal, not the overview's |
| New audit | `/new` | **Scan, then audit the whole system** (D84). First the scope (D85): **an environment**, a folder: the one `opaas ui` started in, detected and pre-filled, or another picked with *Browse…* (a server-side picker that lists folders only, `/api/folders`; one click picks a folder, *Open* goes into it), or **this whole machine**. A scan from the last 5 minutes is shown on arrival only if it is of that same scope; otherwise the page waits for *Scan*. *Scan* launches `opaas discover scan --project <folder>` or `opaas discover scan --well-known` (a real, recorded job; its command shown first); a scoped scan shows the environment's agents (running or not) and models (served or not), and what else the machine serves collapsed beside them; the sentinel meerkat keeps watch until it ends, and a scan from the last 5 minutes is shown without rescanning (D83). What it found — engines with their models, agents that identify themselves — is listed. The one action is *Audit the whole system*: `opaas audit draft --inventory <that scan>` writes the manifest (with the intended use, if given), then `opaas audit run` audits it; unticking the draft audits a manifest you already have. Both commands are shown before they run |
| Batch | `/batch?ids=…` | Several jobs, tracked side by side (D83) |
| Job | `/jobs/{id}` | The job's command, its progress — one block (or one of an audit's six steps) at a time, filling to 100% before the next starts (D83) — its output log, cancel, and, once it ends, the link to the run it produced |
| Run | `/runs/{id}` | Results per block and category with intervals; *not measured* with reasons; deployment fingerprint; files with hashes; certificate versions to generate and download. For an agent: behaviour with what each line counts, instability, task completion by category, the five auditability dimensions each on its own, bias, toxicity and every run; no draft of its own, since an agent is certified within a system audit |
| History | `/runs` | Every run, filtered by kind, model, backend, fingerprint, OPAAS version, date and status |
| Compare | from `/runs`, as a `compare` run | Tick runs of one kind (models, agents or systems) in the history: the dashboard launches `opaas compare`, and the comparison run's page shows its result per figure, or per cell for systems, side by side, overlapping intervals marked as no difference, figures on different terms marked not comparable, its comparability warnings first (D80) |

`/api/…` serves the same data as JSON, for scripts. It is read-only, apart from launching and cancelling jobs.

**How the invariants show on screen:**

- **No colour scale across categories.** One neutral hue draws every interval; colour marks state (running, failed, not measured), not quality.
- **Categories are not sorted by value.** They keep the order the report gives them, so the page does not rank them.
- **A run that is `evaluated-partial` says so in its header**, with the blocks that were not run.
- **Comparison checks comparability before comparing.** Runs whose detector hash, dataset hash, scenario-set hash or spec version differ show *not comparable* on that figure, with the reason. A cloud baseline never enters a performance table (D22).
- **The interface is in English**, like the CLI and the documentation.

### The meerkat

The dashboard keeps OPAAS's mascot: the pixel meerkat of the cover page, whose tagline is *small sentinel, full audit*. It is drawn from the same grid as the CLI, `cli/sprite.py::MASCOT`, which `scripts/sprite_from_png.py` generates from `docs/img/mascot.png`. It is never redrawn by hand, so a change to the mascot reaches the terminal and the dashboard together, and `test_sprite_matches_png.py` keeps guarding it.

**The icon is the meerkat's head:** the top 14 rows and 22 columns of the grid, from the ears to the neck. It is used, unchanged, as:

- the favicon (one pixel per cell, 22×14);
- the mark in the header beside "OPAAS";
- the app icon.

Pixel art stays legible at 16 px, so no simplified version is drawn.

**The mob comes out when a report is generated.** When a run that writes a report ends `completed` or `partial`:

1. Five meerkats from the same sprite rise out of their burrows, one after another. They are drawn at different sizes, adults and a pup, and some face the other way.
2. The largest, the sentinel, turns its head to look around.
3. The pup fidgets.
4. The run's id and report hash appear: *Report ready*.

The rules:

- **It plays once, for about four seconds, with no sound.** The mob never loops, and it never covers the results: it sits above them and leaves them readable.
- **Reduced motion is respected.** With the system's *reduce motion* setting, the mob is shown already standing, without moving.
- **A failed run gets no celebration.** When a run ends `failed`, only the sentinel comes out and looks around, beside the error. When a run is `interrupted`, nothing comes out.
- **The animation is the only decoration in the dashboard.** It runs on a `<canvas>` with a few lines of vendored script, and it needs no image file, font or network.
- **The terminal has its meerkats too** (D82): the sentinel beside `opaas`'s menu, which moves when the mouse passes over it, and, while an audit runs, a company that comes and goes until the audit ends.
- **The sentinel keeps watch while the machine is scanned.** On `/new`, while `discover scan` runs, the sentinel rises out of its burrow and looks one way and the other until the scan ends, then goes back down. It is the one meerkat that loops, because it stands for work still going on, and it stops with that work (D84). With *reduce motion* it stands still.
- **It is a reaction to a file, not a verdict.** It celebrates that a report exists, never what the report says. The mob is the same for a model whose figures are low as for one whose figures are high (invariant 1).

---

## 8. What is lost

- **A second interface to maintain.** Every new CLI option that a user may need has to reach a form, and a form that lags behind the CLI teaches the wrong command. The command shown on every screen limits the damage: it is always the one that runs.
- **Server-rendered pages are less fluid** than a single-page application. That is the price of no build chain and a small, auditable front.
- **Local is not safe by default.** Section 6 narrows the exposure but cannot remove it. Another process running as the same user can read the token from that user's terminal.
- **The queue serialises.** A user with two engines on one GPU who wants them measured together cannot do it from the dashboard. That is deliberate, because the measurements would be wrong, but it is slower.
- **No multi-user dashboard.** It serves the person who installed OPAAS, on their machine. A team shares runs by sharing the store's directories, not a server.

---

## 9. Settled before writing (2026-10-19)

1. Releases and runs are never replaced (D74, phase 18).
2. A newcomer is guided by the dashboard, and the CLI stays the source of truth.
3. The dashboard is local, for whoever installed OPAAS.
4. History lives in `~/.opaas/runs`. Comparison is OPAAS's own, and MLflow is not used.
5. No signing in the dashboard. Each generated unsigned certificate is a version, kept.
6. The server is the optional `ui` extra. The interface is in English.
7. Sentinel's live traffic was shown; since D84 it is followed from the terminal that runs the proxy.
8. The meerkat stays: its head is the icon, and its mob comes out when a report is generated (§7, *The meerkat*).

## 10. Open questions

1. **Disk growth.** The store never deletes. Should `opaas runs` report the store's size per kind, so a user knows what removing runs by hand would free?
2. **Importing `./runs`.** Old run directories have no `run.json`. Phase 18 copies them in on request. Should their `command` be reconstructed from the report's configuration, or left absent and declared as unknown?
3. **Several stores.** `$OPAAS_RUNS_DIR` points `opaas ui` at another store. Should one server show two stores side by side, or always exactly one?

---

## 11. Phases

The work plan's phases build this document. Each lands as its own pull request with tests that open no socket: the server is driven through Starlette's in-process test client.

| Phase | Builds |
|---|---|
| 18 | §4 `run.json` and the append-only store; `opaas runs` |
| 19 | §4 versioned unsigned certificates |
| 20 | §4 `progress.jsonl` from every long command, with the N4 test |
| 21 | §3 the server, §5 jobs and the per-endpoint queue, §6 security |
| 22 | §7 overview, new audit, live, run, sentinel; the meerkat icon and the mob |
| 23 | §7 history and compare |
| 24 | README, CHANGELOG, release |
