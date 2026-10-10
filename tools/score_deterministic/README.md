# `tools/score_deterministic/` — deterministic, GPU-free MCP axis scorer (FU-058)

A drop-in replacement for the clunky vast SFT-student axis scorer. It classifies the
**7 risk axes from a server's description** — the *same* input the v3.0 student uses —
by posing each axis's rubric as a **choice-classification** to a funded deterministic
decider (**GLiDE**, with **jev** as a swap-in), and emits rows **identical in shape to
`mcp_llm_axis_scores`** so they feed the canonical tier calc unchanged.

No GPU. No idle-bleed pods. No env-injection babysitting. Cheap, deterministic, hosted.

## Files

| File | What |
|---|---|
| `rubrics.py` | The 7 axes + **verbatim** class sets + per-class criteria + ordinal ladders. `rubric_fingerprint()` is the `adapter_sha256`-equivalent (`det-<sha>`). |
| `det_scorer.py` | Per-server scorer + pluggable backend (`--backend glide\|jev`). Network is **injected** (`transport`) so it mocks cleanly. Also `tier_from_rows()` — reuses canonical `gate_rule_v1` + `trust_gate`. |
| `det_validate.py` | **Trust check**: sample N already-scored servers from Postgres, re-score, report per-axis **exact% / neighbour%** vs stored student labels. Read-only. Reports numbers; asserts nothing. |
| `score_deterministic_run.py` | Batch over the never-scored backlog; upsert `mcp_llm_axis_scores` + `risk_tier`. Idempotent. **Dry-run default** (`--apply` to write). |
| `tests/test_det_scorer.py` | Hermetic two-pole tests — no network, no DB, no keys. |

## The 7 axes (verbatim class sets)

```
auth_strength      STRONG | MODERATE | WEAK | UNKNOWN
capability_breadth NARROW | MODERATE | BROAD | UNKNOWN
data_sensitivity   PUBLIC | INTERNAL | SENSITIVE | CRITICAL | UNKNOWN
network_egress     NONE | INTERNAL | EXTERNAL | ARBITRARY | UNKNOWN
maintainer_trust   ESTABLISHED | VERIFIED | COMMUNITY | UNKNOWN_AUTHOR
exploit_surface    MINIMAL | LIMITED | MODERATE | BROAD
overall_risk       LOW | MEDIUM | HIGH | CRITICAL
```

`label_index` is the index into these lists (order is contract). `escalated`/`escalated_to`
fire on `overall_risk` when `P(CRITICAL) >= 0.40` — the `gate_rule_v1` threshold.

## Backend request shapes (found, with source)

### GLiDE — `POST https://api.fastino.ai/v1/systemone`
Header `X-API-Key: <FASTINO_API_KEY>` (AgentVault `towersideglide`). One `choice`
question per axis:

```json
{
  "model": "fastino/GLiDE",
  "state": "<server description>",
  "questions": {
    "data_sensitivity": {
      "type": "choice",
      "instructions": "From the description, how sensitive is the data ...",
      "criteria": {"PUBLIC": "...", "INTERNAL": "...", "SENSITIVE": "...", "CRITICAL": "...", "UNKNOWN": "..."}
    }
  }
}
```

Response:

```json
{"model": "glide",
 "answers": {"data_sensitivity": {"type": "choice",
   "choice": "SENSITIVE", "confidence": 0.9993,
   "probabilities": {"PUBLIC": 0.0001, "INTERNAL": 0.0003, "SENSITIVE": 0.9994, "CRITICAL": 0.0001, "UNKNOWN": 0.0001}}}}
```

`choice` → `label`; `confidence`/`probabilities[choice]` → `p_top`; `probabilities` → `probs`.

**Sources:** <https://docs.fastino.ai/inference/systemone> · [fastino-ai/mintlify-docs#248](https://github.com/fastino-ai/mintlify-docs/pull/248) · cross-ref `GLIDE_DECISION_SURROGATE_POLICY.md` + `glide_propose.py` (zo-fleet-tools#71).

### jev — hosted P(yes)-per-row (key `jevapi`)
jev scores one row at a time returning **P(yes)**. The scorer reuses it as a
multi-class classifier by asking, per candidate label, "does this label apply?",
then **softmax-normalising** the per-label P(yes) into a class distribution → the
identical `{label, p_top, probs}` shape falls out. Request the scorer sends:

```json
{"row": "<description>", "question": "<axis instruction> Specifically: does the label 'SENSITIVE' (...) apply?"}
```
expecting `{"p_yes": <float>}` (or `{"probability": <float>}`). **The exact jev wire
contract was not in this repo** (it lives in the policy doc / memory group
`zo-sentinel/sft-training`). Transport is injected, so aligning it is a one-line edit
in `JevBackend.build_request` + the `p_yes` key — confirm against the live `jevapi`
docs before the jev path is used for real. GLiDE is the primary, confirmed path.

---

## OPERATOR RUNBOOK (run after this PR merges; needs Fly PG + model keys — not in CI)

Prereqs: `pip install psycopg2-binary`, keys exported from AgentVault
(`FASTINO_API_KEY` for glide; `JEV_API_KEY` for jev), and a DSN file (a single line
`postgresql://user:pass@host/db`). Open the Fly proxy first:

```bash
fly proxy 15432:5432 -a mcplookup-db
export FASTINO_API_KEY="$(agentvault get towersideglide)"   # never echo the value
```

### Step 1 — Trust check: is GLiDE good enough? (read-only, no writes)

```bash
python tools/score_deterministic/det_validate.py \
    --dsn-file ~/.secrets/mcplookup.dsn \
    --backend glide --n 300 \
    --student-version v3.0_40974559 \
    --json-out det_agreement_glide.json
```

Reads 300 servers the student already scored, re-scores their descriptions with
GLiDE, prints **per-axis exact% and neighbour%** (neighbour = within one ordinal
step) plus an overall roll-up. **It does not decide** — you read the numbers.
Rough reading guide (your call, not the tool's): high exact% on `overall_risk` +
`data_sensitivity` is the signal that matters most for tiering; a large
exact→neighbour gap means GLiDE is "off by one rung" (usable as a complement, maybe
not a straight replacement). Re-run with `--backend jev` to compare deciders.

### Step 2 — If agreement is good: score the 3,141 backlog deterministically (no vast)

Dry-run first (writes nothing, prints the would-be tier distribution):

```bash
python tools/score_deterministic/score_deterministic_run.py \
    --dsn-file ~/.secrets/mcplookup.dsn --backend glide --limit 3141
```

Then apply (idempotent upsert on `(server_id, axis_name, model_version)`; stamps
`risk_tier` via `gate_rule_v1` + `trust_gate`):

```bash
python tools/score_deterministic/score_deterministic_run.py \
    --dsn-file ~/.secrets/mcplookup.dsn --backend glide --limit 3141 --apply
```

Re-running is safe — it upserts, and only ever touches never-scored rows + their
tiers. No GPU, no pods, no env injection.

## Test (hermetic — this is what CI / a reviewer runs)

```bash
python tools/score_deterministic/tests/test_det_scorer.py       # 13 tests, no network/DB/keys
python -m py_compile tools/score_deterministic/*.py
```

## Guardrails honoured

Add-only (new dir); no frozen files touched; no network/GPU in-test (transport
injected + mocked); no prod writes from the scorer module; DSN is a parameter and all
writes are `--apply`-gated (dry-run default). Tier uses the canonical `trust_gate` —
no composite, no tools/provenance (PR #85 skew lesson, reverted via #86).
