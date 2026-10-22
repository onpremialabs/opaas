# OPAAS reference

Every command, the `run` options you will reach for, the classifiers, everything OPAAS
does not measure, and how the code is laid out. For what each mode does, see the
[guide](guide.md); for what a figure may be used to claim, the [methodology](methodology.md).

- [`run` options](#run-options) · [Command reference](#command-reference) ·
  [Classifiers](#classifiers-faithfulness-and-toxicity) ·
  [What is not measured](#what-is-not-measured) · [Architecture](#architecture)

---

## `run` options

The ones you will reach for. Full list with `opaas run --help`.

| Option | Default | Notes |
|---|---|---|
| `--model` | *required* | Model id exactly as the backend serves it |
| `--backend` | `vllm` | `vllm`, `ollama` or `openai` |
| `--suite` | `performance` | Repeatable, or `all` |
| `--base-url` | adapter's own | Your server endpoint |
| `--output` | the run store | Report file, written beside the store's copy (`~/.opaas/runs/<run-id>/report.json`). An existing file is refused without `--force` |
| `--num-ctx` | server's default | Context window the engine must serve (Ollama) |
| `--max-concurrency` | — | What the engine is configured to serve at once. Declared by you, cross-checked by block A |
| `--capability-max-tokens` | dataset's (IFEval: 700), else `400` | Block B cap; when set, it wins over the dataset's. Cut answers are counted and declared |
| `--safety-max-tokens` | dataset's (XSTest: 320), else `320` | Block D cap; when set, it wins over the dataset's |
| `--suite-concurrency` | `4` | Parallel requests the harness sends while scoring blocks B and D |
| `--load-concurrency` | `4` | Simultaneous requests in block C's determinism-under-load check; `1` skips it and declares it unmeasured |
| `--dataset` | `ifeval,xstest` | Sources (`ifeval`, `xstest`, `squad2`, `bbq`, `rtp`, `esbbq`), local paths, or `opaas-smoke` |
| `--concurrency` | `1,4,16` | Block A concurrency levels |
| `--prompt-words` | `128,1024` | Prompt lengths to sweep |
| `--quantization` | — | Declared quantization, e.g. `GGUF Q4_K_M` |
| `--weights-path` | — | Local weights; hashed, and part of the fingerprint when the server is seen holding them |
| `--offline` | off | Cached datasets and classifiers only |
| `--declaration` | — | Block E declaration (JSON) |
| `--note` | — | Context you state about the run. Repeatable. Published as declared by the operator, never as a finding |
| `--toxicity-model` | pinned default | Your own toxicity weights |
| `--faithfulness-model` | pinned default | Your own faithfulness weights |
| `--classifier-threshold` | profile's own | Threshold for the English classifiers, in [0, 1] |
| `--toxicity-es-model` | pinned default | Your own weights for Spanish toxicity |
| `--toxicity-es-threshold` | profile's own | Threshold for the Spanish toxicity classifier |

## Command reference

| Command | What it does |
|---|---|
| `opaas` | In a terminal, the full-screen menu: every task, its command and its documentation, and what you have and are missing. Any terminal size: in a short one the menu scrolls. `OPAAS_NO_ANIMATION=1` keeps the meerkats still. Without a terminal (piped, CI) it prints the help |
| `opaas run` | Runs blocks A–E and writes the report |
| `opaas show <report>` | Summary of an existing report, with its hash |
| `opaas compare` | Cross-references several reports of one kind (bench, smith or system) under the interval-overlap rule |
| `opaas keygen` | Generates an Ed25519 pair and its DID document |
| `opaas root-document` | Writes the root's DID document from the public keys of its two hardware tokens (`#key-1`, `#key-2`), printing the fingerprints to check (D67) |
| `opaas delegate` | The root delegates credential signing to an issuing key, from a hardware token (`--pkcs11-module`, `token` extra) or a key file (D65) |
| `opaas revoke` | Adds a key to the root's signed revocation list |
| `opaas sign` | Issues the signed credential from a model or system report; `--delegation` signs under the root's name with an issuing key |
| `opaas verify` | Verifies a credential, offline, walking root → delegation → issuing key; `--revocations` names a list |
| `opaas trust` | Lists pinned trust anchors, or pins one (or installs a revocation list) after showing what it is |
| `opaas certificate` | `--run <run-id>`: the audit certificate of a stored run (A4, to PDF), checked by its report's hash, a new version each time; with `--report` and `--credential`, a signed credential's printable certificate |
| `opaas publish` | Static certificate site with in-browser verification and badges |
| `opaas watch` | Checks a certificate still describes the deployment, or every component of a system (exit 0/1/3) |
| `opaas log-verify` | Checks the transparency log |
| `opaas admit` | Checks a report against a public registry's admission rules |
| `opaas compliance` | Writes the block E declaration template |
| `opaas datasets` / `fetch` / `import` | Dataset and classifier cache: status, download, convert |
| `opaas backends` / `suites` / `version` | Adapters, blocks, tool and methodology version |
| `opaas discover scan` / `gaps` / `sign-skill` | Inventory of engines, agents and agent stack (`--project DIR`: one environment's folder instead of the whole host); auditability gap scan; sign a skill for G05 |
| `opaas smith scenarios` / `packs` / `run` | Validate scenarios; list the domain packs; run them against an agent |
| `opaas sentinel observe` | Recording (and opt-in gating) reverse proxy |
| `opaas sentinel approvals` / `approve` / `deny` | The gate's approval queue |
| `opaas sentinel inventory` / `overhead` | Environment watch; proxy overhead benchmark |
| `opaas inquest audit` | Post-hoc audit with three-valued verdicts |
| `opaas swarm analyze` / `attribute` | Flow topology, per-edge figures, faults; attribution subgraph |
| `opaas card build` / `manifest` | Auditability Card; deployment manifest |
| `opaas evidence sign` / `verify` | Sign and verify any evidence document |
| `opaas audit draft` | Draft a system manifest: models, agents, tools and flows the host and records show (`--project DIR`: only one environment's; `--update`: add to an existing manifest, keep what is in it) |
| `opaas audit template` | Scenario templates for the manifest's tools; you write the answers |
| `opaas audit run` | Audit a system from its manifest; write the system report and open its audit certificate |
| `opaas audit validate` / `extract` | Check a system manifest; read any document into measurements |
| `opaas ui` | The local dashboard over the run store (`ui` extra), loopback only |
| `opaas runs list` / `show` / `path` / `import` | The run store: every result, with its command and hashes; copy old `./runs` in |

## Classifiers: faithfulness and toxicity

Install the `classifiers` extra and these dimensions measure themselves: the pinned weights
download once, at a commit revision, and are reused.

| Dimension | Default weights | License |
|---|---|---|
| toxicity | `unitary/toxic-bert` | Apache-2.0 |
| faithfulness | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | MIT |

Your own weights win (`--toxicity-model`, `--faithfulness-model`); any
`AutoModelForSequenceClassification` with a `config.json` works. Before either classifier is
used it is fed control cases with obvious answers: if it does not separate them, the
measurement is **not published**; if the threshold is off, the report suggests the right one.
Faithfulness needs passages (`squad2` or your documents).

---

## What is not measured

Stated as plainly as the report states it:

- **Bias**, unless the dataset includes `bbq` — and then it is choosing between two people,
  in English, as stereotypes are understood in the United States. For agents, only on the
  counterfactual scenarios run.
- **Toxicity**, unless the classifiers extra is installed. Toxicity under provocation also
  needs `rtp`, and is not comparable with the paper's 25-sample figures.
- **Block D in Spanish, beyond what is pinned.** Spanish results are reported on their own
  (`byLanguage`), never pooled with English. Bias needs `esbbq`, and covers Spain's social
  context only. Toxicity uses a separate multilingual classifier, calibrated on Spanish
  controls. Not measured in Spanish: a refusal pair (no licensed Spanish XSTest exists, so
  bring your own with `"language": "es"`), toxicity under provocation (no Spanish set
  with a verifiable license), and faithfulness. Other languages get no toxicity figure at
  all.
- **Faithfulness and hallucination with a passage**, unless the dataset carries passages.
  Scoring is literal: a correct paraphrase counts as an omission.
- **Determinism under every load** — only at the `--load-concurrency` level and companions run.
- **Model-generated code is not executed.**
- **Whether an agent is "safe".** Behaviour in the scenarios run and how auditable the
  deployment is; semantic policies are out of scope.
- **ACR on existing records** — only smith, which has ground truth, measures it.
- **The behaviour of an agent no adapter reaches** — it is listed with a pending adapter.
  Letta, AnythingLLM, AutoGen, CrewAI and OpenHands agents are among these (D69). Nor of
  any framework agent whose tools cannot be rerouted to OPAAS's mock server; and whether a rerouted agent had other tools is declared, not observed.
- **Skill signatures in other schemes** (OpenPGP, minisign, Sigstore) — only OPAAS's own is
  verified, and only under a key you name; the rest are *not observed*, never ok.
- **What a project declares outside the files OPAAS reads** (`--project`): YAML (compose
  files, Dify DSL) and source code are not read, so a model or agent named only there is not
  seen; the scan lists them under `notRead`.
- **Whether an agent runtime's actions need credentials** — it gets one GET to `/`, which
  shows its front door only.
- **Streams in shapes the sentinel does not assemble** — OpenAI, the Responses API and Ollama
  are kept whole; any other keeps its last line, and the record says so.
- **Revocations a verifier has not installed** — the revocation list is read offline, so a
  verifier who never updates it never learns of one; `verify` says when it held no list.
- **General infrastructure security** — OPAAS is not a pentest or an OS hardening audit; it
  looks at what touches the AI system, such as an engine exposed without credentials.

A run that skips blocks is certified as `evaluated-partial`; every gap lands in the report's
`limitations`, and nothing is quietly reported as a zero. Context only you know goes in with
`--note`, printed as declared by the operator.

---

## Architecture

```
src/opaas/
├── core/        evidence and trust: JCS, hashes, credentials, transparency log, stats,
│                report schema, fingerprint, AuditEvent log, structural policies, metrics
├── bench/       model certification: runner, blocks A–E, datasets, classifiers, compare
├── discover/    engine inventory, agent identification, agent stack, gap scan
├── smith/       scenarios, mock-tool oracle, agent adapters (bare-model, http, mcp)
├── swarm/       declared vs observed topology, attribution, fault plans
├── sentinel/    watch (drift), inventory watch, observe/gate proxy, approval queue
├── inquest/     importers (sentinel, OTel, JSONL), three-valued audit, degradation
├── card/        Auditability Card, deployment manifest
├── system/      system manifest, measurements, extractors, system report, draft, watch
├── adapters/    InferenceBackend + vllm, ollama, openai
├── telemetry/   GPU, system and network sampling
├── web/         static site, badges, printable certificates
└── cli/         opaas commands, one group per module
```

Every module writes its result as canonical, signable bytes. The suites only talk to
`InferenceBackend`, never to a specific engine; smith only talks to an agent adapter, never
to a specific framework.
