# OPAAS guide

What each audit mode does, how to run it, and what it writes. The
[README](../README.md) is the short tour; the [reference](reference.md) lists every
command and option; the [methodology](methodology.md) says what may and may not be
claimed from each figure.

- [Audit modes](#audit-modes): model deployment, environment, agent behaviour, agent
  auditability, whole system, continuous monitoring, the local dashboard, the run store
- [Methodology in brief](#methodology)
- [Signed credentials](#signed-credentials)
- [Use cases](#use-cases)
- [Datasets](#datasets)

---

## Audit modes

### 1 · Model deployment

```bash
opaas run --model my-model --backend vllm --base-url http://gpu-box.lan:8000/v1 --suite all --output report.json
```

Runs the five blocks of the [methodology](#methodology) against the endpoint and writes one
report: per category, with confidence intervals, findings about the deployment and declared
limitations. Its SHA-256 is what a certificate certifies. Without `--suite`, only block A
runs.

> **Pin the context window.** Without `--num-ctx`, Ollama serves its own default — usually
> far below what the model supports — and long prompts are trimmed with nothing warning you.
> The report records the window it measured as `servedWindowTokens`.

<details>
<summary>Make a run finish sooner</summary>

A full run is dominated by generation. First check the engine actually serves in parallel —
block A reports it: flat throughput with growing TTFT means the engine is queueing (vLLM
`--max-num-seqs`, `OLLAMA_NUM_PARALLEL`). Declare what you configured and the report
cross-checks it:

```bash
opaas run --model my-model --backend vllm --suite all --max-concurrency 8 --suite-concurrency 8
```

Lowering the generation caps (`--capability-max-tokens`, `--safety-max-tokens`) is the
other lever; every response cut by a cap is counted and declared. Trimming the dataset with
`--limit` is the most expensive lever: the confidence interval pays for it. And run only what
you need: `--suite performance` while tuning the server, `--suite consistency` (seconds)
while checking a context setting.

</details>

<details>
<summary>Compare configurations, cloud baselines, air-gapped runs</summary>

FP16 and Q4 do not fit on one GPU at once, so each is evaluated separately and compared. A
difference whose confidence intervals overlap is not a difference:

```bash
opaas compare --report fp16.json --report q8.json --report q4.json --out comparison.json
```

The same command compares two smith reports (behaviour with intervals, and the five
auditability dimensions) or two system reports (cell by cell over the coverage matrix), one
kind per comparison. Runs on a different spec version, scenario set, dataset or detector are
reported as not comparable on the figures that depend on it, with the reason. A cell measured
in one audit and not in the other is shown as a change in what was audited, not in a figure:

```bash
opaas compare --report agent-v1.json --report agent-v2.json
opaas compare --report audit-1/system-report.json --report audit-2/system-report.json
```

A **cloud baseline** measures the same model on a public endpoint, to know what deploying
locally costs. It refuses performance and compliance, cannot be signed, and is excluded from
performance tables:

```bash
opaas run --model meta/llama-3.3-70b-instruct --backend openai --base-url https://integrate.api.nvidia.com/v1 --api-key $NVIDIA_API_KEY --suite capability --reference-type cloud-baseline --output baseline.json
```

**Air-gapped**: populate the cache on a connected machine with `opaas fetch`, copy
`~/.opaas/cache`, and forbid the network with `--offline`. A missing artifact fails before
the model is touched, naming what to supply.

</details>

### 2 · Environment & configuration

```bash
opaas discover scan --well-known -o inventory.json
```

```bash
opaas discover gaps -i inventory.json --log events.jsonl --skill-key skills.pub.pem -o gaps.json
```

A read-only inventory of the host: engines and versions, ports and bind scope (loopback or
network), served models and their digests, GPUs and driver, MCP servers in the
configurations of known clients (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf,
Continue), tool manifests, skills, containers by image digest, and **agents that identify
themselves** (an A2A agent card, Open WebUI, an agent framework by its OpenAPI title). Agent
runtimes that do not identify themselves get one GET, enough to see whether they ask for
credentials. It records process names, never command lines, and names, never secrets. The gap
scan applies nine versioned rules (logging layer, log integrity, free-form shell or HTTP
tools, unpinned skills, skill signatures, approvals as events, anonymous engines and agent
runtimes, actor chains, image digests): a gap is a missing precondition for auditing, never a
predicted violation. Sign a skill with `opaas discover sign-skill DIR --key skills.pem`; a
signature counts only when it verifies under a key named with `--skill-key`.

**One environment instead of the whole host.** When several deployments share a machine, name
the one to audit by its folder:

```bash
cd ~/projects/support-bot
opaas discover scan --project . -o inventory.json
```

OPAAS then reads that folder's own MCP configurations (`.mcp.json`, `.vscode/mcp.json`,
`.cursor/mcp.json`…) and skills (`.claude/skills`, `skills/`) instead of the user's and the
host's containers, and the files that declare its agents: the graphs of a `langgraph.json`, an
A2A agent card, and the n8n or Flowise workflow exports (`*.json` in the folder or in
`workflows/`, `flows/`, `chatflows/`…) that contain an agent node, with the models their model
nodes name. It reads those fixed places only, never crawls. The host's engines and listeners
are still observed, because that is where the environment's models and agents run; the
inventory's `project` section says which of them belong to it: each agent *running* at an
identified endpoint or *not running*, each model *served* by an engine here or not. YAML (a
compose file) and source code are not read, and the scan says so: a model named only in code
is not seen. The scope is part of the environment hash.

Every model report also records the **deployment fingerprint** — engine version, model
digest, chat template, GPU driver — and everything else the host serves.

### 3 · Agent behaviour

```bash
opaas smith run --agent mcp --endpoint http://localhost:9000/mcp --scenarios scenarios/ -n 3 -o smith.json
```

Fixed scenarios with **mock tools that record every call**, so the correct outcome is known
without a judge: task completed, forbidden tool attempted, injection resisted, schema-valid
calls, step budget, destructive action without confirmation, instability across repeats.
Adapters: `http`, `mcp` (OPAAS is the agent's MCP client and the MCP server of its tools),
`bare-model` (any model in OPAAS's own tool loop), and `a2a`, `open-webui`, `langgraph`,
`langserve`, `dify`, `flowise` and `n8n` for
agents you configure beforehand to use OPAAS's mock MCP server as their only tools:

```bash
opaas smith run --agent a2a --endpoint http://localhost:41000 --tools-listen 127.0.0.1:8765 --scenarios scenarios/ -o smith.json
```

The agent's tools must point at `http://127.0.0.1:8765/mcp` for the run: an agent whose tools
cannot be pointed there is not driven, because its scenarios would act on real tools. Two
agent-level measurements come with it:

- **Bias** from counterfactual scenarios — the same task with one personal attribute changed,
  actions compared, a difference counted only when each value is stable across repeats.
- **Toxicity** of the agent's final answers, by the same calibrated classifier as block D.

21 reference scenarios ship with OPAAS, and five domain packs (`banking`, `health`,
`public-sector`, `rag`, `devops`; `opaas smith packs`, then `--scenarios pack:banking`), each
also in Spanish (`pack:banking-es`…) with the same tools, and each pinned by hash. A result on them alone is flagged not certifiable: they test a domain, not
your deployment. `opaas audit template` writes skeletons for your own tools, aiming
injections at the tools that act and pairing risky tools with a lookup, and leaves every
expected answer to you. The report carries answer digests, never their text, unless `--keep-finals`.

### 4 · Agent auditability

```bash
opaas sentinel observe -u http://127.0.0.1:11434 --log events.jsonl -p policies.json --key ops.pem
```

```bash
opaas inquest audit events.jsonl -p policies.json --key ops.pub.pem -o audit.json
```

The **sentinel** is a reverse proxy in front of the engine: every call becomes a
hash-chained `AuditEvent` sealed with an operational key, bodies kept as digests. Callers say
who they act for with `X-OPAAS-Actor-Chain: user:alice>agent:planner`. `--keep-output` keeps a
response field in clear; a stream is assembled first, so the whole answer is kept. `--mode gate`
(opt-in) blocks what policies forbid and holds actions for a named human approver, notified
with `--notify-webhook` or `--notify-command` by the action's id and digest, never its content. **Inquest** audits
existing records — OPAAS logs, OpenTelemetry GenAI spans, JSONL — integrity first, then every
structural policy as **comply, violate or undecidable**, each undecidable naming the fields
that would settle it. **Swarm** compares declared multi-agent flows with observed ones,
per-edge latency and errors, and contained versus cascaded faults. **Card** answers the
Auditability Card's Q1–Q6 from that evidence.

```json
[{"id": "confirm-before-delete", "kind": "precedes", "enforce": "approve",
  "scope": {"field": "action", "eq": "tool_call"},
  "match": {"field": "target", "eq": "delete_file"},
  "before": {"field": "action", "eq": "approval"}}]
```

### 5 · Whole system

```bash
opaas audit draft -o system.json --records events.jsonl
```

```bash
opaas audit draft --project . -o system.json      # one environment, not the whole host
```

```bash
opaas audit template system.json -o scenarios/
```

```bash
opaas audit run system.json -o out/ --member llm=report.json
```

The **system** is the audited subject: a purpose, the components that serve it and the flows
between them. `audit draft` **identifies the components itself** — every served model, the
agents that identify themselves, the MCP servers of known clients, and, from the records of
the running system, every agent that acted, the models and tools it actually called, and the
flows. `audit run` runs only what the manifest calls for (bench per model, smith per agent,
sentinel per proxy, inquest and swarm per record source), cites documents you already have
with `--member`, and writes a system report: members by hash, declared against observed
composition, a **components × dimensions table** where every cell is *measured*, *not
measured* or *not applicable* with its reason, Q1–Q6, block E for the system's purpose, and
your thresholds judged **comply, violate or undecidable**. An agent OPAAS cannot drive yet is
kept with a pending adapter — listed, never dropped. With `--project` (or an inventory scanned
with it) the draft takes only that environment's models and running agents; everything else
the host serves, a declared agent that is not running, and a named model no engine here serves
are left out with a note each.

### 6 · Continuous monitoring

```bash
opaas watch --report report.json --credential credential.json --key keys/opaas-root-ed25519.pub.pem --log watch.jsonl
```

A one-shot check for cron or a timer: the deployment fingerprint first (no inference), then
canary prompts compared by hash with those signed at certification, then coarse statistics
when those could not decide. On a system certificate it re-checks every component and names
the one that moved. Exit `0` current, `1` drifted or expired, `3` inconclusive, `2` bad
inputs. Nothing is published; `--log` keeps a hash-chained history.

### 7 · Local dashboard

```bash
uv sync --extra ui
```

```bash
opaas ui
```

Open the address it prints: the token in it is the session's key. The dashboard serves your
run store on `127.0.0.1` only. It shows every run with its command and its files checked
against their hashes, per-category intervals, what was not measured, live progress, and
versioned audit certificates. It launches measuring commands only, each shown
before it runs, and jobs against one engine wait their turn. The *Overview* says what OPAAS
measures and what an audit runs. *New audit* scans one environment, the folder `opaas ui` was started from
or one chosen with *Browse…*, or the whole machine (the sentinel meerkat keeps watch until the
scan ends), lists the models and agents it found, and audits them as one system: it drafts the manifest from that scan, with the intended use if you give it, then runs
`opaas audit run`. A single model or agent is audited from the terminal (`opaas run`, `opaas smith
run`). An agent run shows its behaviour, its task completion by category and the five
auditability dimensions. A system shows its coverage matrix and Auditability Card. Each shows
what was not measured and why. *Runs* filters the history and compares model, agent or system
runs through `opaas compare`, per figure, with no ranking.

What it never does: sign, generate or read a key, delegate or revoke, start or follow the sentinel proxy
or approve a held action, or delete a run. Those stay in a terminal (`opaas sign`, `opaas
sentinel approve`). It computes no figure of its own, and it does not listen beyond
`127.0.0.1`: from another machine, tunnel to it:

```bash
ssh -L 8780:127.0.0.1:8780 your-host
```

### Every result is kept

Every command that produces a result also records it in the **run store**:
`~/.opaas/runs/<run-id>/`, or `$OPAAS_RUNS_DIR`. A run directory holds the command's
documents and a `run.json` that records:

- the exact command, with secrets replaced;
- the OPAAS version, the start and end times, and the status;
- the hash of every file.

Files are written once and made read-only. An existing `--output` path is refused unless you
pass `--force`, and even then the store keeps the earlier result.

```bash
opaas runs list
```

```bash
opaas runs show 20261019-003
```

`show` checks every file against the hash the run recorded. Runs written to `./runs` by
earlier versions are listed by `opaas runs import`, and copied in with `--copy`; the originals
are never moved. Nothing in OPAAS deletes a run.

---

## Methodology

**Principles.** Every figure is per category with a Wilson 95% interval; a difference whose
intervals overlap is not a difference. Scoring is deterministic — pattern matching whose hash
travels in the report, and small local classifiers pinned to a commit, hashed like any other
artifact and **calibrated on control cases before they are believed**. Datasets are pinned
by SHA-256. Prompts go through the chat template. Operator declarations are never added to
what OPAAS verifies. Every result is bound to the environment it was measured in. The full
methodology, and what may **not** be claimed from each figure: [docs/methodology.md](methodology.md).

### Model blocks A–E

| Block | `--suite` | What it measures |
|---|---|---|
| **A** Performance | `performance` | Throughput, TTFT and latency p50/p95/p99, VRAM and RAM, concurrency scaling, Wh per million tokens, findings about the deployment |
| **B** Capability | `capability` | Accuracy per category (IFEval instruction following, multiple choice), unparsed answers apart from wrong ones |
| **C** Consistency | `consistency` | Determinism serially and **under concurrent load**, robustness to nine adverse prompts, the real context window with a canary |
| **D** Behaviour & safety | `safety` | Correct refusal vs over-refusal (XSTest), hallucination with a passage (SQuAD 2.0), faithfulness, toxicity and **toxicity under provocation** (RealToxicityPrompts), bias per social category (BBQ) |
| **E** Compliance | `compliance` | Operator declaration (license, provenance, AI Act risk class, logging, air-gapped) beside OPAAS's own probes (logprobs, reasoning, egress) |

<details>
<summary>Block details</summary>

**A — Performance.** A `prompt length × concurrency` sweep. Ratios become findings, such as
`no-concurrency-scaling` when the engine queues instead of serving in parallel.

**B — Capability.** Accuracy per category, never aggregated. Instruction following uses
programmatically checkable constraints, broken down by which instruction the model ignores.

**C — Consistency.** Determinism with a fixed seed, serially and with the same request sent
among `--load-concurrency` other users' requests. Robustness measures that the engine does not
break, not the answer's quality. A lost canary is attributed, from token counts, to the
engine trimming the prompt or the model losing attention.

**D — Behaviour and safety.** Refusal and over-refusal on the same items with the same
detector — apart, because a model that refuses everything scores 100% on both. Hallucination
asks for something the passage does not contain; answerable and unanswerable are reported
apart. Bias: per BBQ category, abstention when the context does not say, accuracy when it
does, and the share of unjustified picks that follow the stereotype, flagged only when that
interval lies wholly above 50%. Toxicity of ordinary responses and of continuations of
provoking openings, reported apart and per band; long responses scored in windows.

**E — Compliance.** A requirement declared met without evidence is reported `unsupported`. If
air-gapped is declared and the egress probe sees external traffic, the report states the
contradiction. Write the declaration with `opaas compliance --out declaration.json`.

</details>

### Agent auditability

The five dimensions of *Auditable Agents* (Nian et al.), with metrics a third party can
recompute:

| Dimension | Metrics | Card question |
|---|---|---|
| Action recoverability | ACR, RF | Q1 — what happened? |
| Lifecycle coverage | LPC, GB | Q2 — in what context? |
| Policy checkability | SPDR, ADL | Q3 — did it comply? |
| Responsibility attribution | AC, ACD | Q4 — who was responsible? |
| Evidence integrity | IS, VC | Q5 — can the evidence be trusted? |
| Degradation | audits of damaged records | Q6 — what is lost when logs are incomplete? |

Verdicts are three-valued (comply, violate, undecidable — never rounded). Policies are
structural predicates over recorded fields, never questions about what a text means. Ground
truth comes from instrumentation, never from a model. Evidence is digests by default.
Thresholds are yours: OPAAS never sets one.

---

## Signed credentials

Every run's own certificate is the **audit certificate**: `opaas certificate --run
<run-id>`, the dashboard's *Generate certificate*, or the end of `opaas audit run`. It is
self-attested, carries the report's SHA-256 and no signature (D81). The commands below are
for **issuing a signed W3C credential** over a report, with a key a verifier already trusts.
That is how onpremIA Labs will issue its own certificates once phase 10 is integrated, and
how an organisation can sign under its own name today.

| You run | You get | It shows |
|---|---|---|
| `opaas run` → `sign` | **`OpaasModelCertificate`** | One deployment: a line per block A–E, complete or partial |
| `opaas audit run` → `sign` | **`OpaasSystemCertificate`** | A system: purpose, components, the components × dimensions table, thresholds, and whether it is **certifiable** — with every reason when it is not |
| `opaas evidence sign` | **`OpaasEvidence`** | That one document (inventory, smith report, audit…) is unaltered — not a certificate of fitness |

**Always the same:** a W3C credential, Ed25519 over the SHA-256 of the report's bytes
(`eddsa-jcs-2022`, RFC 8785), verified **offline** with `opaas verify`, valid **90 days**,
with a printable A4 certificate and QR code (`opaas certificate`), a static site with
in-browser verification and badge (`opaas publish`), an optional hash-chained issuance log,
every declared limitation, and **never an overall score**. A system is certifiable only when
every member was measured in one environment, every agent ran your own scenarios, no
component is a cloud baseline, no bench report is partial and no step failed.

The certified hash is the hash of the report's bytes, so checking that a report is the
certified one is `sha256sum report.json`. The issuance log is **not** Certificate
Transparency: no Merkle trees, no independent monitors; it proves only that the published log
is internally consistent, which is why publishing it in a repository with history matters.
The certificate page verifies the signature in the browser, with the credential and the DID
document embedded, so it never asks the issuer whether a certificate is good.

`verify` never takes the issuer's key from the credential: `--key` or `--did-document`, then
pinned anchors (`opaas trust --add did.json`), then those bundled with the tool. By default a
key signs under its own name (`did:key`); to sign as your organisation, generate it with
`--did did:web:yourdomain.com` and publish `did.json` there. The root key is the whole root of
trust: see [key custody](key-custody.md).

An offline root can also delegate (D65): `opaas delegate` has it sign, from a hardware token,
a delegation naming an issuing key and a window of months; `opaas sign --delegation` embeds it,
and `verify` (and the browser) accept the issuing key only once that delegation verifies under
an anchor, covers the signature's date, and neither key is on the root's revocation list
(`opaas revoke`, installed with `opaas trust --add revocations.json`). The production root is
created in a ceremony written out in [the ceremony runbook](ceremony.md).

---

## Use cases

- **Choosing a quantization, an engine or a GPU.** Evaluate each configuration and `compare`:
  only differences whose intervals do not overlap count.
- **Capacity planning.** Block A's concurrency sweep says how many simultaneous users the
  deployment serves before latency hurts, and whether the engine queues.
- **Regulatory evidence (AI Act, ENS, ISO/IEC 42001, NIST AI RMF).** Block E separates what
  you declare from what is verified, cited to the clause; the system certificate binds it to
  the system's purpose. See the [standards mapping](standards-mapping.md).
- **Accepting an agent before production.** Your own scenarios with smith: injections,
  destructive actions without confirmation, permission bait, counterfactual bias.
- **Auditing a multi-agent system.** Sentinel records, inquest verdicts and swarm attribution
  answer who did what, under which policy, and whether the record can be trusted.
- **Hallucination on your own documents.** Compose a dataset of your passages in: what
  matters on-prem is whether the model makes things up about your documentation.
- **Checking a vendor's claims.** A signed report anyone can recompute, instead of a
  benchmark nobody can reproduce.
- **After every upgrade.** `opaas watch` says whether a new driver, engine or re-pulled model
  changed what was certified, and which component moved.
- **Sovereign and air-gapped deployments.** Everything runs locally, `--offline` forbids the
  network, and the egress probe checks the air-gapped claim.

---

## Datasets

Real datasets by default, downloaded on first use, verified against a pinned SHA-256 and
cached in `~/.opaas/cache`:

| Source | Block | What it adds | License |
|---|---|---|---|
| `ifeval` | B | Instruction following, 510 programmatically checkable items | Apache-2.0 |
| `xstest` | D | 250 benign prompts that look dangerous + 200 harmful ones | CC-BY-4.0 |
| `squad2` | D | Hallucination with a passage, 200 questions, half unanswerable | CC-BY-SA-4.0 |
| `bbq` | D | Social bias, 1,800 questions across 9 categories | CC-BY-4.0 |
| `rtp` | D | Toxicity under provocation, 200 openings of the challenging subset | Apache-2.0 |
| `esbbq` | D | Social bias in Spanish (Spain's context), 2,000 questions across 10 categories | Apache-2.0 |

The default is `ifeval,xstest`; `start.sh` uses the five English ones. Each task declares its
language, and block D reports every language apart (D70). Sources compose into one dataset —
a certificate is issued from one run — and your own documents can be mixed in
(`--dataset ifeval,xstest,/data/internal-docs`). `--dataset opaas-smoke` validates the
pipeline and is flagged as measuring nothing. Format and subsets: [docs/datasets.md](datasets.md).
