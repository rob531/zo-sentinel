# Fly-PG bulk-scoring campaign tooling

Self-contained, parameterized-DSN tool suite to score the **~214,396 never-scored**
servers in **Fly Postgres** (`mcplookup-db` · `mcp_server_registry`, ~540k rows,
`risk_tier` NULL/`''`/`unassessed`). It reuses the proven `tools/rescore/` pieces
(prompt, escalation gate, trust-gate) but re-points **source + sink at Fly PG**.

> The existing `tools/rescore/weekly_rescore.py` already targets Fly PG, but it is a
> stateful weekly-cadence daemon. This suite is a **stateless, wave-driven,
> dry-run-by-default** campaign driver for the one-off 214k backfill.

## Pieces

| Tool | Role |
|---|---|
| `score_campaign_export.py` | Select the never-scored distinct-URL backlog; emit **description-only** eval_phase2 JSONL. Read-only. `--limit` for waves, `--out`. |
| `score_campaign_fire.py` | Guarded GPU launcher (geo-exclude, MAX_DPH cap, RTX_4090→fallback, adapter-sha pin, auto-destroy, generous timeout). **Dry-run by default.** |
| `score_campaign_ingest.py` | Upsert preds → `mcp_llm_axis_scores`, compute `risk_tier` via **gate_rule_v1 + trust cap**. Idempotent, resumable, **dry-run by default** (`--apply` to write). |
| `score_campaign_db.py` | DSN seam: sqlite (hermetic test) vs Fly Postgres (prod, via `fly proxy`). |
| `test_score_campaign_twopole.py` | Hermetic sqlite two-pole gate. No PG / proxy / network / GPU. |

## Contracts reused verbatim (no re-derivation, no skew)

- **Prompt** — `tools/rescore/prompt_system.txt` + `prompt_signals_block.txt` (the
  exact description-only prompt the v3.0_40974559 student was SFT'd on). **No**
  tool-manifest / fingerprint / provenance block — that skew is why PR #85 is wrong.
- **Escalation** — `tools/rescore/calibration.escalation_gate` (p_crit≥0.40→CRITICAL,
  p_crit+p_high≥0.30→REVIEW).
- **gate_rule_v1 tier** — CRITICAL-escalation override → else argmax(overall_risk);
  REVIEW is a queue flag that does **not** change the stored tier (mirrors
  `tools/rescore/apply_risk_tier_backfill.py`).
- **Trust cap** — `trust_gating_override.trust_gate` (verified/official publisher +
  HIGH/CRITICAL → MEDIUM).
- model_version `v3.0_40974559`, adapter_sha256 `bf842f54..`.

## OPERATOR RUNBOOK (run from the Tower)

```bash
# 0. one-time: deps + the DSN file (credentials only; socket is the proxy)
pip install psycopg2-binary vastai
printf 'postgresql://USER:PASS@mcplookup-db/DBNAME' > _dsn.txt   # keep out of git

# 1. open the Fly proxy (leave running in its own shell)
fly proxy 15432:5432 -a mcplookup-db

# 2. EXPORT one wave of the never-scored backlog (read-only)
python tools/score_campaign/score_campaign_export.py \
    --dsn-file _dsn.txt --limit 40000 --out wave01.jsonl.gz
#    -> "never-scored distinct-URL backlog: ~214000 representatives"
#    -> writes wave01.jsonl.gz (40000 eval_phase2 records)

# 3. FIRE a guarded GPU pod to score the wave (needs the adapter dir + VAST_API_KEY)
#    (bundling wave01 + adapter onto SCORE_BRANCH is the same git-transfer step
#     weekly_rescore.ph_bundle does; the pod pulls it via vast_score_onstart.sh)
python tools/score_campaign/score_campaign_fire.py \
    --adapter-dir /path/to/v3.0_40974559_FULL/final        # DRY-RUN: verify + plan
python tools/score_campaign/score_campaign_fire.py \
    --adapter-dir /path/to/v3.0_40974559_FULL/final --search   # cheapest eligible
python tools/score_campaign/score_campaign_fire.py \
    --adapter-dir /path/to/v3.0_40974559_FULL/final --launch   # rent+run+auto-destroy

# 4. WAIT, then collect preds.jsonl.gz from RESULTS_BRANCH (git fetch the branch)

# 5. INGEST the wave (dry-run first, then --apply)
python tools/score_campaign/score_campaign_ingest.py \
    --preds results/preds.jsonl.gz --dsn-file _dsn.txt            # DRY-RUN
python tools/score_campaign/score_campaign_ingest.py \
    --preds results/preds.jsonl.gz --dsn-file _dsn.txt --apply    # WRITE

# 6. repeat 2–5 per wave until EXPORT reports 0 backlog representatives.
```

### Recommended parameters & cost (full 214k)

- **Wave size:** `--limit 40000` → ~6 waves. Keeps each pod ~2h, well under the caps.
- **MAX_DPH:** `0.45` $/hr (the proven cap — do not raise).
- **Cost:** anchored on the reference `--full` run (~66k servers ≈ **$1.2** at 0.45/hr,
  ~2.7h). Extrapolated: **214k ≈ ~$3.9 total**, ~9 GPU-hours. Budget ~$1/wave.
- **Inference timeout:** default **21600s (6h)** per pod — never the 1800s that caused
  the 2026-10-08 shepherd scar. The tool refuses any value < 3600s.

### Resume / idempotency

- `score_campaign_export.py` only ever selects rows with **no** v3.0 score, so a
  re-export after a partial campaign returns only what is still outstanding.
- `score_campaign_ingest.py` is **delete-then-insert per (model_version, server_id)**
  with a deterministic tier — re-running a wave (after an interruption, or twice) is
  safe: identical rows, identical tiers, no duplicates (proven by pole (c)).

## Hermetic test

```bash
python tools/score_campaign/test_score_campaign_twopole.py   # no PG/proxy/GPU
```
