# Changelog

Every change that alters what OPAAS measures, writes or certifies is recorded
with its reasoning, and what it costs, in [`docs/decisions.md`](docs/decisions.md);
the `D<n>` numbers below point there. This file says what changed between
releases, for someone deciding whether to upgrade.

The methodology version embedded in every report (`OPAAS_SPEC_VERSION`,
`1.0-draft`) is separate from the package version, and did not change.

## [Unreleased]

For 0.4.0: the run store, first of the pieces behind the local dashboard
(D75, D76).

### Upgrading

- **Results are now recorded in `~/.opaas/runs`** (or `$OPAAS_RUNS_DIR`).
  `opaas run` without `--output` writes its report there, no longer into
  `./runs` in the working directory. With `--output`, and for every other
  command's `-o`, the file is still written where you asked, and the store
  keeps a copy.
- **An existing output path is refused** with exit 2, by `run`, `smith run`,
  `audit run`, `discover scan`/`gaps`, `inquest audit`, `swarm analyze`,
  `card build`/`manifest`, `sentinel overhead` and `compare`. Pass `--force`
  to write over it; the store keeps the earlier result regardless. Scripts
  that write to the same path twice need one or the other.
- **Old `./runs` directories** are listed by `opaas runs import`, and copied
  into the store with `--copy`. The originals are not moved.

### Added — the run store (D76)

- Every result is kept in its own run directory, claimed atomically, with
  files written once and made read-only. `run.json` records:
  - the command, with secrets and URL credentials or query strings removed;
  - the OPAAS version, the times and the status;
  - the hash of every file.
- `opaas runs list | show | path | import`. `show` checks each file against
  its recorded hash.
- `docs/dashboard.md`: the design of the local dashboard `opaas ui`, and the
  file contract between it and the CLI (D75).

### Added — versioned unsigned certificates (D77)

- `opaas certificate --run <run-id>` renders the unsigned draft of a stored
  run's report, model or system, without a credential. Each call is a new
  version (`certificate-v1.html`, `-v2`…), kept as a run of its own. The
  report is checked against the hash its run recorded first.
- A model report now has a draft certificate page, like a system report.

### Added — progress written into each run (D78)

- Every recorded command writes `progress.jsonl` into its run directory:
  start and end for all, the blocks of `run`, the scenarios of `smith run`
  and the steps of `audit run`. It is an operational log, not evidence, and
  carries no prompt, response, key, URL credential or query string.

### Added — `opaas ui`, the local dashboard (D79)

- `opaas ui` serves a dashboard over your run store on `127.0.0.1:8780`.
  Install it with the `ui` extra (`uv sync --extra ui`, or
  `pip install "opaas[ui]"`). Open the address it prints: the token in it is
  the session's key.
- It shows:
  - the machine's status;
  - every run, with its command and files checked against their hashes;
  - per-category intervals and what was not measured;
  - live progress;
  - versioned unsigned drafts, which you can open or download.
- It launches measuring commands only: a model run or a machine scan from a
  form, and a draft certificate from a run. Each shows its exact command
  first. Jobs against one engine wait their turn. Signing and keys stay in
  the terminal.
- The meerkat stays: its head is the icon, and its mob comes out when a run
  writes a report.
- **Agents in the dashboard.** *New audit* runs `smith` against any
  adapter, on the reference scenarios, a domain pack or your own directory.
  A rerouted agent needs your confirmation that its tools are OPAAS's mock
  server. An agent's run page shows:
  - its behaviour, with intervals;
  - task completion by category;
  - the five auditability dimensions, each on its own;
  - bias and toxicity, and every run;
  - each absence, with its reason.
- The dashboard listens on port 8780 by default: 8765 is where `smith`
  serves the mock tools of rerouted agents.

## [0.3.1] — 2026-10-19

Everything in Spanish that 0.3.0 left out so that it shipped entirely in
English: block D in Spanish (D70) and the Spanish domain packs (D73). It is
also the first release built and attached by the release workflow, with its
wheel, sdist and checksums (D74).

> **The Spanish domain packs are an unreviewed translation.** They are
> OPAAS's own translation of the English packs, not yet reviewed by native
> domain experts (D73). Use them to see whether the language of the
> conversation changes what an agent does, not as a domain expert's
> rendering of Spanish banking, health or public-sector practice.

### Upgrading

- **Block D's detector hash changed** (D70). The refusal and abstention
  detector now carries Spanish patterns next to the English ones, and
  `patterns_hash()` covers them. Every block D report has a new
  `detectorHash`, English-only ones included. Reports from before and after
  are not comparable under the D11 rule: re-run both sides with the same
  version before comparing.
- **Cached datasets are re-imported once.** `MANIFEST_VERSION` is 3: tasks
  now declare their `language`. The first run after upgrading rebuilds each
  cached dataset from the already-verified source files. Nothing is
  downloaded again.
- **Your own datasets should declare a language.** A task without
  `"language"` is scored as English, as before, and the report now says it
  declared none. Add `"language": "es"` to Spanish tasks: they are then scored
  with the Spanish classifier and reported under `byLanguage.es`.
- **`--classifier-threshold` applies to the English classifiers only.** The
  Spanish toxicity classifier has its own `--toxicity-es-threshold`.

### Added — block D in Spanish (D70)

- **`esbbq`**: EsBBQ, Spanish social-bias questions for Spain's social
  context, pinned at a commit of the authors' repository with ten hashed
  files. 2,000 items over ten categories, asked in Spanish, the options
  reordered by item id so the unknown option is not always C.
- **Results per language.** Every block D figure is computed within one
  language. English stays at the top level, and every other language gets
  `byLanguage.<tag>` with its own refusal pair, toxicity and `notImplemented`.
  The CLI, the site, the printable certificate and the system cells show the
  language on every row.
- **A Spanish toxicity classifier**: `textdetox/xlmr-large-toxicity-classifier`
  pinned at a full commit, safetensors only (about 1.1 GB), calibrated on six
  Spanish control cases and unpublished when it does not separate them.
  `--toxicity-es-model` and `--toxicity-es-threshold` override it. The
  English classifier never scores a Spanish response.
- **Spanish refusal, abstention, compliance and dissuasion patterns**, matched
  on every response alongside the English ones.
- **Not measured in Spanish, and declared:** a refusal pair (no licensed
  Spanish XSTest exists), toxicity under provocation (no Spanish set with a
  verifiable license), and faithfulness.

### Added

- Spanish versions of the five packs, `pack:banking-es` and so on: the same
  situations and tools, with Spanish tasks, data, injections and answer
  patterns. Every `pack.json` declares its `language`, and reports list it
  beside each pack's version and hash (D73).

### Releases are kept, never replaced (D74)

- Every release now carries its wheel, its sdist, `SHA256SUMS` and
  `BUILD-INFO.txt`. A workflow builds them from the tag with a pinned `uv` and
  build backend, so rebuilding the tag gives the same bytes.
- `v*` tags can no longer be moved or deleted, and a published release can no
  longer gain or swap files. A mistake is fixed by the next patch version.
- 0.2.0 and 0.3.0 gain archives built from their tags after the fact, as their
  `BUILD-INFO.txt` says. 0.2.1 is tagged at its release commit (`e20a404`).
- The procedure is in [`docs/releasing.md`](docs/releasing.md).

## [0.3.0] — 2026-10-18

Phase 14 of the work plan: the operational gaps the status table declared
left out (D64). Phase 10, steps 2 and 4: the root delegates to an issuing
key, and keeps a revocation list (D65, D66). Phase 10, step 3 prepared: the
ceremony runbook and its rehearsal (D67). Phase 11: smith drives
A2A, Open WebUI and LangGraph agents whose tools are rerouted to its mock MCP
server (D68), then LangServe, Dify, Flowise and n8n agents (D69). Phase 13:
domain scenario packs and kind-aware templates (D71). `audit run` ends with
the draft certificate its report will become (D72).

This release is entirely in English. Block D in Spanish (D70) and the
Spanish domain packs (D73) are built, but wait for the next release.

### Upgrading

- **`certifiable` is computed from the operator's scenarios.** A smith report
  is certifiable when scenarios of your own ran, which is what "not only the
  reference set" meant before; reports on domain packs alone are not (D71).
- **`audit template` writes different injection and permission skeletons**
  when the manifest declares tools that act: the planted instruction names
  one, and the template forbids them. Templates already completed are never
  overwritten; regenerate into a new directory to compare.
- **`verify` prints new checks.** Every verification against a DID document
  or an anchor now begins with `anchor`. A credential carrying a delegation
  adds `delegation`, `delegation-validity`, `revocation` and `delegated-key`.
  Scripts that parse the check list should expect them; credentials without a
  delegation verify exactly as before, with the same verdict.
- **Revocation lists are read beside the anchors.** `verify`, `certificate`,
  `watch` and `evidence verify` read every list in
  `~/.opaas/anchors/revocations/` (or `$OPAAS_ANCHOR_DIR/revocations/`) for
  the credential's issuer. A list there that the issuer's root did not sign
  fails verification for that issuer: remove stray files.
- **The browser verifier recognised no model certificate.** It looked for
  `OpasModelCertificate`, so the `structure` check failed on every model
  certificate page. Republish your site with `opaas publish` to ship the
  fixed `verify.js`.
- **`verify_credential` takes a `TrustRoot` or a key.** Passing a public key
  still works and still means *trust this key directly*. `sign_credential`
  accepts any signer with `public_key()` and `sign()`; `key_id` is optional
  when a delegation is given.

- **G05 changed meaning.** A skill with a signature file used to be `ok`. It
  is now `ok` only when signed with `opaas discover sign-skill` and verified
  under a key named with `discover gaps --skill-key`; with no key named, or a
  signature in another scheme, it is `not-observed`. Gap scans say ruleset
  version 2 and carry a new ruleset hash: compare them with version 1 scans as
  a different instrument.
- **Skill hashes leave out `signature.json` and `SIGNATURE`**, as they already
  left out `.sig` and `.asc`. An inventory of a skill that carried one of
  those files hashes differently, and `sentinel inventory` against an older
  baseline reports that skill as changed: re-baseline after upgrading.
- **G07 now judges agent runtimes**, which `discover scan` probes with one GET
  to `/` (`agentProbes` 2). A runtime answering anonymously on a non-loopback
  bind is a new gap. The environment hash does not change.
- **A2A, Open WebUI and LangGraph agents are no longer pending.** A system
  manifest that declares one is driven by `audit run` once the component
  names `tools_listen`, the address of OPAAS's mock MCP server the agent was
  configured with (and `model` for Open WebUI). Without it the agent stays
  pending, with a reason that now says what to declare; the report quotes
  each pending step's own reason.
- **LangServe, Dify, Flowise and n8n agents are no longer pending either**
  (D69), on the same terms; a Flowise agent also names its chatflow id as
  `assistant`. A manifest still declaring `letta`, `anythingllm`, `autogen`,
  `crewai` or `openhands` stays valid and pending.
- **The sentinel's kept output for streams** is now taken from the assembled
  response, so streamed tool calls keep their names under the defaults; a
  non-JSON body that is not a stream no longer records `streamed: true`.

### Added

- `opaas delegate`: the root signs a delegation to an issuing key, from a
  PKCS#11 hardware token (`--pkcs11-module`, new `token` extra) or a key
  file. `opaas sign --delegation` and `opaas evidence sign --delegation`
  embed it, and refuse one that has expired or names another key (D66).
- `opaas root-document`: the root's DID document from the two tokens'
  public keys, with the fingerprints to check over a second channel (D67).
- `docs/ceremony.md`, the production key ceremony step by step, and
  `scripts/rehearse_ceremony.py`, which runs every `opaas` step of it with
  throwaway keys: on two SoftHSM2 tokens when `SOFTHSM2_MODULE` is set,
  with key files otherwise (and in `pytest`).
- `opaas revoke`: the root's signed revocation list. `opaas trust --add`
  installs one after it verifies under a pinned anchor; `verify
  --revocations` names one explicitly; `opaas publish --revocations` gives
  it to the browser.
- `verify.js` walks the same delegation chain as `opaas verify`, and a test
  runs it under Node to pin that both reach the same checks.
- `opaas discover sign-skill` and `discover gaps --skill-key`: verified skill
  signatures (G05).
- `opaas sentinel observe --keep-output`: response fields kept in clear; a
  stream is assembled before they are picked (OpenAI chat and completions,
  Responses API, Ollama), and marked when it could not be or was cut.
- `--notify-webhook` and `--notify-command` for `--mode gate`: approvers are
  told an action is held, by its ids and digest only. The pending event
  records whether each notice was delivered.
- `discover scan` probes agent runtimes and prints what each answered; G07
  covers them.
- `opaas smith run --agent a2a | open-webui | langgraph`, with
  `--tools-listen`, `--assistant` and `--agent-token` (OPAAS_AGENT_TOKEN,
  never written to the report). The manifest takes `tools_listen` and
  `assistant` (D68).
- `opaas smith run --agent langserve | dify | flowise | n8n` (D69). Each
  scenario starts a new conversation; `--assistant` is the Flowise chatflow
  id; Dify needs the app key in `--agent-token`.
- `audit run` writes and opens `certificate-draft.html`: the system
  certificate over its report, unsigned and marked a draft on every sheet.
  With `--certificate-base-url` it carries a QR code to
  `<base>/cert/<id>/index.html`, where the issued certificate will be
  published; without it, none (`--open/--no-open`) (D72).
- `opaas smith packs` and `--scenarios pack:<id>`: five domain scenario
  packs (`banking`, `health`, `public-sector`, `rag`, `devops`), pinned by
  version and hash, with counterfactual pairs for triage, hiring and
  moderation. A manifest names them in `scenarios` as `pack:<id>` (D71).

## [0.2.1] — 2026-10-15

A patch release. **Upgrade if you installed 0.2.0 without the `classifiers`
extra:** `opaas audit run` failed there before running anything.

### Upgrading

- **Nothing to migrate.** Reports, credentials and certificates written by
  0.2.0 verify unchanged, and the methodology version is the same.
- **`discover scan` now sends a few read-only GETs to listeners that are not
  engines** (D63), looking for agents that identify themselves. A scan of the
  same host may therefore find agents 0.2.0 did not; an inventory in which
  none is found keeps the hash 0.2.0 gave it.

### Fixed

- `opaas audit run` imported `click`, which OPAAS does not depend on: typer
  stopped requiring it in 0.17, and it was only present when the
  `classifiers` extra pulled it in through `huggingface-hub`. A test now fails
  on any import of a package `pyproject.toml` does not declare.

### Added — identifying a system's agents (D63)

- `opaas discover scan` recognises agents that identify themselves: A2A agent
  cards, Open WebUI, and OpenAPI documents whose title names an agent
  framework. Each one found says on what basis; `--agent-endpoint` names a
  remote one.
- `--well-known` reads the MCP configurations of common clients (Claude
  Desktop, Claude Code, Cursor, VS Code, Windsurf, Continue) from their usual
  files, names only, never a value.
- `opaas audit draft --records` drafts the agents that acted in a sentinel
  log, OTel spans or JSONL, with the models and tools each one called and the
  flows between them.
- An agent smith cannot drive yet (`a2a`, `open-webui`, a framework, or known
  only from records) is part of the system with a pending adapter: its
  behaviour reads *not measured* with the reason, and the system is not
  certifiable until it is measured.

### Documentation

- The README is rewritten around what OPAAS audits, the audit modes, the
  methodology and the certificates each one produces.

## [0.2.0] — 2026-10-15

The first tagged release. 0.1.0 was never tagged: it is the model
certification of D1–D27 (blocks A–E, signed W3C credentials, the hash-chain
issuance log, the static site). 0.2.0 makes OPAAS audit a **system** — the
models, agents, tools and records that serve one purpose — and issues one
certificate for it.

### Upgrading

- **Reports and certificates from 0.1.0 still verify.** `opaas verify` accepts
  `OpaasModelCertificate` as before, next to the new `OpaasSystemCertificate`.
- **Smith reports now carry a digest of the agent's final answer, not the
  text** (D62). Reports written before keep the text: review them before
  publishing.
- **The public identifier on badges, QR codes and certificates changed** from
  `onpremialabs:cert:…` to the issuer's domain, or `opaas:cert:…` for a
  `did:key` issuer (D58). The signed credential id is unchanged.
- **Inventory hashes** differ from 0.1.0's on a host where Ollama had a model
  loaded (D61): `opaas watch --inventory` against an old baseline may report
  that once.
- **A run with `rtp` and the default load check takes longer**: 200 more
  generations, and five rounds of concurrent requests in block C (D59).

### The system audit (D60)

- `opaas audit draft` writes a system manifest from what the host serves.
- `opaas audit validate` checks it; `opaas audit template` writes scenario
  templates for its tools, leaving every answer to the operator, and smith
  refuses a template still marked `TO COMPLETE`.
- `opaas audit run` runs only what the manifest calls for (discover, bench per
  model, smith per agent, sentinel per proxy, inquest and swarm per record
  source), cites documents the operator already has (`--member`), and writes
  the system report: members by hash, declared against observed composition,
  one cell per component and dimension (measured, not measured or not
  applicable, always with a reason), the Auditability Card Q1–Q6, block E for
  the system's purpose, and operator thresholds judged comply, violate or
  undecidable.
- `opaas audit extract` reads any OPAAS document into the common measurement
  format.
- `OpaasSystemCertificate`, signed, verified, printed and published with the
  existing commands; certifiable only when every member shares one
  environment, every agent ran the deployment's own scenarios, no component is
  a cloud baseline, no bench report is partial and no step failed.
- `opaas watch` on a system certificate re-checks every component and names
  the one that moved.

### Agents (D41–D52, D60)

- One evidence core (`core/`): the `AuditEvent` schema, hash-chained and sealed
  logs, structural policies with three-valued verdicts, and the auditability
  metrics ACR, RF, LPC, GB, SPDR, ADL, AC, ACD, IS and VC.
- `discover` (inventory and gap scan), `smith` (fixed scenarios with mock-tool
  ground truth; `bare-model`, `http` and `mcp` adapters), `swarm` (declared
  against observed flows, fault injection), `sentinel` (observe and gate proxy,
  approvals), `inquest` (post-hoc audit, degradation for Q6) and `card`.
- Agent-level bias from counterfactual scenarios, compared on actions and
  undecidable when the agent is unstable within one value, and toxicity of the
  agent's answers (D60, S4).

### Model certification

- Hallucination with a passage from SQuAD 2.0, answerable and unanswerable
  reported apart (D53).
- Bias with BBQ, per social category, never pooled (D55).
- Toxicity under provocation with RealToxicityPrompts' challenging subset,
  reported per band and apart from ordinary responses (D59); long responses
  scored in windows rather than truncated (D56).
- Determinism under concurrent load in block C, `--load-concurrency` (D59).
- The deployment fingerprint and the host inventory in every report (D34, D40),
  and `opaas watch` between certifications (D33, D36).
- A printable certificate with a QR code (D29), 90-day validity (D28), operator
  notes printed as unverified (D31), and block E references cited to the clause
  (D30).

### Fixed

- Keys signed as onpremIALabs unless told otherwise (D39).
- Block E probes reported what they did not observe (D38).
- The operator's token cap was ignored in favour of each item's (D54).
- The sentinel lost its last seal on SIGTERM, and accepted an actor chain it
  could not complete (D57).
- One environment was read as two because of Ollama's model runner and mixed
  hash granularities (D61).
- Smith reports carried the agent's answers verbatim (D62).

[0.2.1]: https://github.com/luxinopanyvino/opaas-bench/releases/tag/v0.2.1
[0.2.0]: https://github.com/luxinopanyvino/opaas-bench/releases/tag/v0.2.0
