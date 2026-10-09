# Scorer discrimination + automated label loop — design of record (2026-10-09)

**Referent gaps:** RC-4 (the live scorer cannot discriminate) and RC-5 (the SFT
label loop that would fix it is dormant), from the UNKNOWN-rate RCA
(api-job `unknown-rate-20261009T184915Z`, GR-7 evidence trail).
**Gap classes:** GC-8 (echo verification — a signal that echoes the output),
GC-1 (no ignorance token / INSUFFICIENT never escalates), GC-6 (verdict
vocabulary drift, a third dialect found below), GC-10 (a dormant loop whose
dormancy nothing watches).
**Grade claimed by this document: C0→C1 for the design-in-hand** (mechanism
named with evidence at every line; scaffold staged). C2 requires the origin
fixes merged; C4 requires the acceptance gate armed in the lane. Per
`LOCO_CHAIRMAN.md` §3, nothing here is "closed".

**Execution split:** this doc + scaffold are control-plane and carry **no GPU
or scoring compute**. Everything a GPU lane needs to execute without
re-deciding anything is in §3–§5.

---

## 1. Mechanism of non-discrimination (RC-4), with evidence

### 1.1 The live signal layer is a 70-prior echo chamber

The only production signal writer is `signal_analyser.py` (confirmed by
`docs/AXIS_SENSE_CHECK_2026-08-28.md` §7: it "emits `url_safety`,
`tool_security`, `supply_chain`, `reputation`, `domain_trust` and `composite`,
and has never emitted" the other canonical types — those are test fixtures).
All five signals start from the same prior and apply bounded keyword nudges:

| signal | weight | prior | real information carried | why |
|---|---:|---:|---|---|
| `reputation` | 0.20 | 70.0 | **zero (circular)** | §1.2 |
| `tool_security` | **0.25** | 70.0 | **~zero in fallback: 3 distinct values** | `signal_analyser.py:239` hardcodes `tools = []` ("tools column not in registry schema"), so the fallback score is a function of description length only: {50, 55, 60}. The enricher path (`enrichers/discrimination_enrichers.py`) regex-matches *registry metadata*, not actual tool manifests — `mcp_tool_hashes` is empty (§1.3), so no tool evidence exists for ANY server. |
| `url_safety` | 0.20 | 70.0 | low | penalties only for non-https / suspicious TLD / keyword hits (`signal_analyser.py:190-227`); the registry population is overwhelmingly https github/npm URLs, so most rows sit at the 70 prior. |
| `supply_chain` | 0.20 | 70.0 | low, and the wrong kind | driven by `scan_count` (`signal_analyser.py:287-298`) — **a pipeline artifact measuring how often *we* scanned the row, not any property of the server** — plus a trusted-source bonus nearly every row gets. |
| `domain_trust` | 0.15 | 70.0 | some | ±20 on a trusted-domain list (`signal_analyser.py:354-406`); again, most registry rows are github/npm → the bonus is near-universal, so it separates the population poorly *within itself*. |

`compute_composite_score` is a weighted mean of five near-70 numbers and
returns exactly **70.0 when no weight matches** (`signal_analyser.py:433-434`).
`score_to_verdict` maps `[70, 85) → TRUSTED_RESEARCH`
(`signal_analyser.py:409-419`). That band is where a five-signal 70-prior mean
lands by construction — which is why the scored share of prod is
~16% `TRUSTED_RESEARCH` and why the in-repo oracle fires (§1.5).

**Quantification:** of the composite's total weight 1.00, `reputation` (0.20)
carries zero information and `tool_security` (0.25, the *highest-weighted*
signal) carries ~zero in the fallback that runs whenever the enricher import
fails (`signal_analyser.py:11-15` imports from `/home/workspace`-pathed
`enrichers.…`, so deployment layout decides which code runs — itself a GC-5).
**≥45% of composite weight is degenerate; the remaining ≤55% is low-variance
keyword nudging around a shared prior.**

On the axis plane (`docs/AXIS_SENSE_CHECK_2026-08-28.md` §7 final table):
**3 of 7 axes abstain 100%** (`auth_strength`, `capability_breadth`,
`data_sensitivity` — "no production evidence source has ever existed"),
`maintainer_trust` abstains 77%, and the honest count of axes carrying real
information today is **4 of 7** — and two of the six *input* axes have
normalised MI with `overall_risk` of 0.02 and 0.06 (§2 table, bus plane).

### 1.2 The reputation circularity — confirmed, and worse than static

`compute_reputation_score` (`signal_analyser.py:317-351`) reads
`server.trust_score` and **returns it**: `score = trust_score` when >50,
`trust_score * 0.8` when ≤50, prior 70 otherwise, ±10/+5 for name/description
*length*. Meanwhile `process_server` writes the composite **back into that
same column**: `UPDATE mcp_server_registry SET trust_score = {composite} …`
(`signal_analyser.py:570-574`).

So this is not merely a static echo — it is a **cross-pass feedback loop**:

```
pass N:   composite_N  ->  registry.trust_score
pass N+1: reputation = trust_score = composite_N   (weight 0.20)
          composite_{N+1} = 0.20·composite_N + 0.80·(static keyword signals)
```

Three consequences:

1. **No information is added** — a signal derived from the output cannot
   discriminate beyond what the other signals already said (the exact rule 3
   of `tools/axis_scorer_grounded.py`: "A derived signal is not evidence";
   its audit found `reputation`'s evidence is `existing_trust:X` on *every*
   sampled row, and 110/150 `maintainer_trust=ESTABLISHED` grades rested on
   `reputation` alone).
2. **High scores self-lock**: `trust_score > 80 → score = trust_score` exactly
   — the echo branch has no damping above 80.
3. **The staleness gate hides it**: `HASH_FIELDS`
   (`signal_analyser.py:41`) excludes `trust_score`, so the input-hash skip
   treats a changed trust_score as "inputs unchanged"; only the 7-day
   `MAX_AGE_DAYS` forced pass re-runs the echo. The circularity is therefore
   invisible to the freshness instrumentation.

### 1.3 The constant fingerprint — the hash of nothing

`docs/AXIS_SENSE_CHECK_2026-08-28.md:63-87` measured it: across **all 3,316
`mcp_fingerprints` rows**, `tool_name_hash` and `permission_scope_hash` take
**one** distinct value — `e3b0c44298fc1c…b855` = **SHA-256("")**. The traced
chain (same doc, §6a, lines ~296-319): `mcp_tool_definitions` never existed →
`get_server_tools()` returns `[]` → hashing `""` yields a well-formed 64-hex
constant → the `tool_count` signal scores 91.95 ± 1.36 for every server →
`capability_breadth` and `auth_strength` have no real evidence → v3, with no
ignorance token, labels them confidently anyway → 99.47% of the prod corpus
lands in the top two risk bands.

Status today: `hash_or_absent()` / `is_absent_hash()` landed (hash of nothing
is `None`; the 3,316 written rows are readable as absent), but
**`mcp_tool_hashes` is still empty — the fix is correct-but-unexercised.**
Nothing crawls MCP servers for tool manifests. A provenance signal over empty
input is a constant, and a constant cannot discriminate.

### 1.4 Extension: a THIRD verdict dialect (GR-7 gets worse)

`score_to_verdict` (`signal_analyser.py:409-419`) emits
`{TRUSTED, TRUSTED_RESEARCH, REVIEW, HIGH_RISK, BLOCKED}`. Of those five, only
`TRUSTED_RESEARCH` appears in the documented 7-term vocabulary
(`TRUSTED_GENERAL … INSUFFICIENT`, CLAUDE.md "Verdicts reference"), and none
match the production-dominant lowercase `unknown`. So the system now has at
least **three** live dialects: documented, scorer-emitted, ingestor-default.
Any corpus built from raw `verdict` column values inherits this split — which
is why §2.3 pins the label vocabulary before a single teacher call is made.
(Taxonomy is HELD: this design *selects* the documented 7-term set as the
corpus label space; it does not redefine it.)

### 1.5 The in-repo degeneracy oracle — use it, don't rebuild it

`anomaly_detector.py:217-276` (`detect_score_clustering_anomaly`) already
formalises "scoring is not discriminating": HIGH when corpus-wide trust-score
`stddev < 0.01` (its evidence string at :249 literally says "Scoring is not
discriminating."), MEDIUM when ≥90% of servers share one 0.1-wide bucket
(`CLUSTERING_THRESHOLD = 0.90`). The acceptance gate (§2.4) reuses these
thresholds as the RED poles and adds separation requirements for GREEN —
the scaffold implements it as a pure function
(`zo_sentinel/label_loop/gate.py`) so both the lane and tests can run it
without the bus.

---

## 2. The restructure — making the student scorer discriminate

### 2.1 Break the circularity at the origin (not at the symptom)

1. **Remove `reputation` from `SIGNAL_WEIGHTS`** and stop emitting it from
   registry-derived trust. Renormalise the remaining weights. If a reputation
   signal is wanted later it must be **external-evidence-only** (github_stars,
   download counts, fork/issue cadence via the ingestion lane) and must never
   read `trust_score` or any column this pipeline writes.
2. **One-writer rule for `trust_score`** (GC-14): `process_server` is the only
   writer, and **no signal may read it**. Enforce with a greppable gate:
   `grep -n "trust_score" signal_analyser.py` must show writes only
   (test asserts `compute_*` functions never touch the key).
3. **Student input feature exclusion list** (hard, enforced at corpus build):
   `trust_score`, `verdict`, `reputation`, `composite`, and any
   `mcp_signal_scores` row whose evidence fails the
   `axis_scorer_grounded.py` rules (derived, fixture, `is_absent_hash`).
   The student must never see the incumbent's answer in its features —
   otherwise distillation reproduces the echo at training scale.

### 2.2 Replace the empty-fingerprint signal with a real one

The empty-hash *defense* exists; the missing piece is **tool-manifest
ingestion** — without it three axes stay evidence-starved forever (the axis
doc's own closing line: "no amount of rubric or scorer work substitutes for
it").

- **Producer:** a crawler lane (ZoComputer-side, via `emit_directive`) that
  speaks MCP `tools/list` to each registry URL (through the allowed egress
  path only), writing to `mcp_tool_hashes`:
  `{server_id, tool_name, description_hash, input_schema_hash, captured_at}`.
- **Fingerprint definition:** `tool_manifest_fingerprint = SHA-256(` canonical
  JSON of the sorted list of `(tool_name, input_schema_hash)` pairs `)`,
  computed with `hash_or_absent()` — **absent/empty manifest → `None`, never a
  hash**; consumers read `None`/`is_absent_hash` as INSUFFICIENT, not as a
  score.
- **Derived signals this unlocks:** real `permission_scope_score` (declared
  tool input schemas), real `tool_description_safety` (the enricher finally
  pointed at actual tool names/descriptions instead of the server's one-line
  registry description), `capability_breadth` (count + diversity of tools),
  and **drift**: a fingerprint *change* between captures is a first-class
  temporal event (feeds GR-24's events store).
- Until the crawler fills the table, the three starved axes **keep
  abstaining** — that is the honest output; the corpus (§2.3) encodes the
  abstention so the student learns it rather than hallucinating tiers.

### 2.3 The distillation corpus — exact contract

Format: `messages`-format JSONL per `zo_sentinel/sft/schema.py` (what
zomesh-sentinel-sft consumes). One row per (server, teacher pass):

```json
{"messages": [
   {"role": "system", "content": "<fixed rubric prompt, versioned>"},
   {"role": "user", "content": "<EVIDENCE BLOCK: raw registry fields + grounded signals w/ evidence blobs + tool manifest or ABSENT marker>"},
   {"role": "assistant", "content": "<JSON: {verdict, axis_labels{...}, confidence, evidence_citations[...]}>"}
 ],
 "metadata": {"server_id": "...", "batch_id": "...", "teacher": "<model@version>",
              "label_source": "teacher|threat_registry|synthetic_hard_negative",
              "corpus_version": "v4"}}
```

**Label space (selects, does not redefine — taxonomy HELD):** the documented
7-term verdict vocabulary `TRUSTED_GENERAL, TRUSTED_RESEARCH,
ENTERPRISE_CONTROLLED, CAUTION_LIMITED, HIGH_RISK_ISOLATED, KNOWN_THREAT,
INSUFFICIENT`, plus per-axis labels from
`schemas/risk_axis_mapping_v1.json` — **every axis carries
`INSUFFICIENT_EVIDENCE` as a first-class label with declared direction**
(the two bugs rule: direction is required; it cannot be inferred from words).

**Label distribution — floors, not targets.** The failure to prevent is a
corpus that teaches the prior (82% unknown in → a student that says UNKNOWN
out). Per 1,000-row batch after teacher labelling:

| class | floor | source when organic rows are short |
|---|---:|---|
| INSUFFICIENT | 15% | organic — evidence-starved rows, labelled honestly |
| KNOWN_THREAT | 8% | `mcp_threat_associations` join (real threat intel) + documented typosquat exemplars |
| HIGH_RISK_ISOLATED | 8% | servers whose *tool manifests* (or descriptions, pre-crawler) match the `DANGEROUS_TOOL_PATTERNS` / escalation sets |
| CAUTION_LIMITED + ENTERPRISE_CONTROLLED | 15% | organic mid-band |
| TRUSTED_* | 20% | high-evidence rows (stars + age + manifest + clean scan) |
| unconstrained remainder | 34% | organic distribution |

A batch that cannot meet a floor is **recorded as unbalanced in its manifest**
(not silently padded); the retrain trigger (§3.6) requires the *cumulative*
corpus to satisfy floors before enqueueing, so unbalanced batches accumulate
until balance exists. No silent caps (GC-5).

**Hard negatives — the discrimination teeth.** Five constructions, ≥10% of
each batch combined, each tagged `synthetic_hard_negative` in metadata:

1. **Trusted-domain + dangerous payload**: real github/npm-hosted rows whose
   tool set matches dangerous patterns — breaks the `domain_trust` shortcut
   (today worth +20/+25 unconditionally).
2. **Near-duplicate pairs**: same registry_source, near-identical description,
   one with threat association / one without — forces evidence-reading over
   metadata priors.
3. **Typosquats** of the top-trusted names (edit-distance-1 names, fresh
   domain age) — must NOT inherit the namesake's trust.
4. **Client-mislabelled-as-server** (the `Spring-AI-MCP-Client` catch from the
   Gemini pilot) — correct answer INSUFFICIENT/not-a-server, not HIGH.
5. **Evidence-null rows** (no url, no description, no manifest) — correct
   answer INSUFFICIENT on every axis; any tier emission is a training error.

**The teacher signal.** Teacher = a frontier LLM reached **only via
`inference_router :8773`** (architecture constraint: no other outbound HTTP).
The teacher prompt: (a) legitimises refusal explicitly (the pilot showed
refusal-legitimacy is what made Gemini's 80% INSUFFICIENT on `overall_risk`
*meaningful*); (b) requires per-axis labels + confidence + **evidence
citations into the provided evidence block** — a row whose citations don't
resolve against its own evidence block is rejected at validation
(anti-hallucination); (c) is versioned, and the version is stamped in every
row's metadata. **Disagreement protocol:** 10% of each batch is double-labelled
by a second teacher config; pairwise agreement < 60% on that slice fails the
batch (teacher drift guard). Teacher INSUFFICIENT **is** a training label
(abstention is taught, not filtered out).

### 2.4 Acceptance metric — the discrimination gate

Implemented in `zo_sentinel/label_loop/gate.py` (pure, hermetic; thresholds
from the §1.5 oracle). Run on the student's scores over the **held-out,
stratified eval slice** (10% of corpus, never trained on, frozen per
corpus_version). ALL must hold for GREEN:

| check | threshold | pole it kills |
|---|---|---|
| spread | score stddev ≥ **10.0** (0–100 scale) | the 70-prior collapse (oracle RED at <0.01; 10.0 is the *acceptance* floor, far above the pathology) |
| anti-clustering | max share of scores in one 0.1-bucket < **0.50** (oracle fires ≥0.90; accept at <0.50) | "one score for everyone" |
| separation | mean(TRUSTED_*-labelled eval) − mean(KNOWN_THREAT-labelled eval) ≥ **25.0 points**, and pairwise AUC ≥ **0.80** | a spread that doesn't order risk |
| abstention calibration | on the evidence-null eval slice: INSUFFICIENT rate ≥ **0.90**; on the full eval slice: ≤ **0.35** | both failure poles of the ignorance token — never abstaining (v3) and always abstaining |

RED on any check → the adapter is **not promoted**, a `mesh_event`
(`sft_gate_red`) is emitted with the failing numbers, and the job lands
FAILED with the gate report in its history. The gate's two-pole test ships in
the scaffold (degenerate scores → RED; discriminating → GREEN), so the gate
itself is verified before any GPU minute is spent.

---

## 3. The automated label loop (RC-5) — exact design

### 3.0 Current dormancy, confirmed

`zo_sentinel/sft/README.md:22-37`: two latches (enabled-latch:
`SFT_BATCH_ENABLED=1` / `.batch_enabled` sentinel / constructor; dispatcher
latch: default `NoopDispatcher` records intent, never launches —
`batch_runner.py:50-63`). Nothing generates jobs: ingestion is live, intake is
empty. Last corpus rebuild attempt **failed 2026-04-29 10:27 UTC: "MiniMax
returned empty"** (`GENERATION_FAILURES.md`, `rebuild_signal_analyser` entry)
— and nothing guarded the corpus against an empty return being appended.

### 3.1 Data flow

```
mcp_server_registry (append-only)
   │  count crosses watermark + 1000           [trigger.py  — RowTrigger]
   ▼
batch (start, end]  — deterministic batch_id = sha256("v4:{start}:{end}")[:16]
   │  fetch rows via ws_query (bus) / zobridge
   ▼
teacher pass via inference_router :8773        [loop.py — injected teacher_fn]
   │  JSONL rows, per-row schema validation + citation check
   ▼
corpus append — segment file per batch         [corpus.py — CorpusStore]
   │  EMPTY/UNDERSIZED RETURN → quarantine, never appended   (§3.4)
   ▼
retrain check: corpus grew ≥ 2000 validated rows since last DONE job
   │  + cumulative label floors satisfied (§2.3)
   ▼
JobSpec (method=sft, dataset=corpus snapshot, deterministic job_id)
   │  DRY-RUN: log would-enqueue, stop        [loop.py --dry-run]
   ▼
IngestQueue.submit → QUEUED
   │  single flag ZO_SFT_LABEL_LOOP_ARMED=1   [dispatcher.py — build_runner]
   ▼
BatchRunner + ShellDispatcher → GPU lane runs training   (HELD — lane-owned)
   ▼
gate.py on held-out eval → GREEN promote adapter / RED mesh_event + FAILED
```

### 3.2 Trigger — every 1k registry rows

- Watermark file `<state_dir>/label_loop_state.json`
  (`{"watermark": N, "attempts": {batch_id: k}}`), written atomically
  (temp + `os.replace`, same discipline as `ingest.py`).
- `pending_batches(current_count)` yields `(start, end]` windows of 1,000 from
  the watermark up to `floor(count/1000)*1000`. The registry is append-only
  (architecture constraint), so row count is monotonic and windows are stable;
  batch membership is pinned by `ORDER BY <ingest ordinal> LIMIT/OFFSET` at
  fetch time and recorded (server_id list hash) in the corpus manifest, so a
  re-fetch that drifts is detectable.
- The watermark advances **only** on batch resolution (appended, or
  failed-out per §3.4) — never on dispatch of work.

### 3.3 Idempotence — every stage, not just the trigger

| stage | key | re-run behaviour |
|---|---|---|
| batch | `batch_id = sha256("v4:{start}:{end}")[:16]` | same window → same id |
| corpus append | segment file `segments/<batch_id>.jsonl` + manifest row | segment exists → no-op (`duplicate`) |
| retrain enqueue | `job_id = "sft_" + corpus_manifest_sha[:12]` | same corpus state → same job_id → `IngestQueue._find` hit → skip |
| dispatch | job status directory (QUEUED→CLAIMED→RUNNING) | the runner claims a job exactly once |

The loop tick is therefore safe to run on any cadence, concurrently with a
previous crash at any point: the single-instance PID lock plus
resolution-only watermark advance means the worst case is a re-fetch and a
no-op append.

### 3.4 Failure handling — the 2026-04-29 empty return must not poison

`CorpusStore.append_batch` **rejects** (quarantines to
`quarantine/<batch_id>.json` with the reason, appends nothing) when ANY of:

- rows is empty — *the exact 2026-04-29 "MiniMax returned empty" case*;
- rows < `min_fraction` (default **0.5**) of the batch's server count;
- any row fails schema validation (`messages` non-empty, every `content`
  non-empty, assistant payload parses as JSON with a verdict **in the
  canonical 7-term set** — the dialect gate from §1.4);
- (lane-side, §2.3) citation resolution or double-label agreement fails.

A rejected batch increments `attempts[batch_id]`; the watermark does **not**
advance, so the next tick retries. After **3** attempts the batch is
failed-out: watermark advances, a `failed` manifest row records it (no silent
drop — GC-5), a `GENERATION_FAILURES.md`-style entry is appended, and a
`mesh_event` (`label_loop_batch_failed`) is emitted. An UNKNOWN cannot rest
unescalated (GC-1): the queue-depth/staleness alarm below catches a loop that
fails every batch.

**Loop liveness (GC-10):** the tick writes `service_health` heartbeats like
every other daemon; `directive_queue_guardian`-pattern check: state-file
mtime older than 2× cadence, or quarantine count > 5 with zero appends in
24h → red alert. The dormant loop's dormancy is itself monitored.

### 3.5 The single flag

`ZO_SFT_LABEL_LOOP_ARMED=1` is **the one documented switch**
(`zo_sentinel/sft/dispatcher.py:build_runner`). Open, it (a) enables the
`BatchRunner` and (b) selects the real `ShellDispatcher` (which shells out to
the configured dispatch command — the sft repo's `dispatch_vast_v3.sh` /
`install_sky_dispatcher.sh`). Closed (default), everything is dormant and the
dispatcher is `NoopDispatcher`. The two legacy latches still exist underneath
(defense in depth — `ShellDispatcher` *independently* refuses to launch when
the flag is closed, and honours per-job `dispatch.dry_run`), but no operator
ever has to coordinate them again: **one flag, one doc line, one grep**.

### 3.6 Retrain trigger

Enqueue a retrain JobSpec when **both**: validated corpus rows grew ≥ **2,000**
since the dataset snapshot of the last non-FAILED job, **and** cumulative
label floors (§2.3) hold. JobSpec: `method=sft`,
`student = {base_model: qwen2.5-3b, init_adapter: <current>, output_name:
student_v<n+1>}`, `dataset.train_path = <corpus manifest snapshot>`,
`dispatch = {backend: vast, dry_run: False, recipe: sft_v3_dpo.yaml}`,
`resources.accelerators = {"RTXA5000": 1}` (the shapes
`tests/test_sft_ingest.py` already pins). DPO follow-up batches (preference
rows from gate-RED vs gate-GREEN adapter outputs) are a later corpus_version,
not v4.

### 3.7 What is HELD vs. runnable now

| runnable now (this PR, dry-run) | HELD (lane/chairman) |
|---|---|
| tick, trigger, corpus append, quarantine, gate evaluation on any score file, queue submit in dry-run | opening `ZO_SFT_LABEL_LOOP_ARMED`, any `ShellDispatcher` real launch, GPU training, teacher API spend, adapter promotion |

---

## 4. The exact GPU-lane runbook (no re-deciding)

```bash
# 0. preconditions (on the lane host, zo-sentinel checkout at origin/main after scaffold merges)
python -m pytest tests/test_label_loop_scaffold.py -q        # must be green
python -m zo_sentinel.label_loop status                       # dormant, watermark visible

# 1. dry-run the tick against the live registry count (reads bus, writes nothing)
python -m zo_sentinel.label_loop tick --dry-run               # logs batches + would-enqueue

# 2. teacher pass + corpus build (spend: inference_router; budget per chairman/CREDIT_LEDGER.md)
python -m zo_sentinel.label_loop tick                         # un-armed: builds corpus, enqueues job, does NOT dispatch

# 3. arm + dispatch (THE held step — chairman authorisation required)
export ZO_SFT_LABEL_LOOP_ARMED=1
export ZO_SFT_DISPATCH_CMD=/path/to/zomesh-sentinel-sft/dispatch_vast_v3.sh
python - <<'EOF'
from zo_sentinel.sft.dispatcher import build_runner
from zo_sentinel.sft.ingest import IngestQueue
print(build_runner(IngestQueue("zo_sentinel/sft/queue")).drain())
EOF

# 4. after training: gate on the held-out eval scores (RED exits 1 — wire into the lane's alert path)
python -m zo_sentinel.label_loop gate --scores eval_scores.json
```

## 5. Acceptance + register

- Scaffold PR: **STAGED, not merged** (execution-plane; GPU HELD).
- Register: new row **GR-28** (this mechanism), classes GC-8/GC-1/GC-6/GC-10,
  grade C1 on this doc + staged scaffold; GR-7 row touched (dialect finding
  §1.4 appended to its evidence trail).
- C2 evidence: reputation removed at origin + exclusion list enforced, merged.
- C4 evidence: gate GREEN required for adapter promotion in the lane workflow,
  with disarm-hardening (#4128 pattern) on the gate itself.
