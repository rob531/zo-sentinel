# DESIGN: Change Intelligence (score change-over-time as a first-class signal)

Chairman directive 2026-07-18: *"the study of changes over time should inform
improvements to the corpus and the 7-axis scoring (refinements for SFT and
generally inference correlation between different datapoints)."*

## Why now (evaluated gap, 2026-07-18)

- `mcp_llm_axis_scores` is DELETE-then-INSERT per `model_version`
  (weekly_rescore flush): a rescore within a version **destroys** the prior
  value. The app has no data to answer "what changed?"
- The only mounted change surface is `perspective_diff_service` (trust-diff,
  known UI no-op) and `server_compare_api` (cross-server, not cross-time).
- Built-but-orphaned modules (`mcp_definition_history_api`,
  `trust_score_time_series`, `fleet_risk_tier_trend_api`, `trend_analyser`,
  `definition_change_*`) prove demand but none are wired to real capture.

## What this PR ships

1. **Migration 0009** - `score_change_events` (CHANGED rows only: label_index
   flip or escalation flip; prev+new label/index/p_top) and
   `score_change_runs` (per run x axis aggregates incl. UNCHANGED counts).
   Storage discipline for the 1GB Fly PG: stability is stored as aggregates,
   never per-row; events volume ~= weekly flips only (est. 2-8% of refreshes).
2. **Capture at the only honest chokepoint** - inside `weekly_rescore.py`
   flush(), which holds prev and new rows in the same transaction. Fail-open:
   if the tables are missing or the insert fails, the batch is retried without
   events and capture disables for the run (`RESCORE_CAPTURE_DELTAS=0` is the
   explicit kill switch). Scoring is never blocked by analytics. No-loss
   invariants I1/I2 untouched.
3. **`tools/rescore/delta_report.py`** - the "study" consumer: flip rates per
   axis per run, label-transition matrices, axis co-flip lift, SFT candidate
   list, per-source instability. Read-only.

## The feedback loop (how change data feeds back)

- **SFT refinement** (sibling repo `rob531/zomesh-sentinel-sft`): repeat-flip
  and low-confidence-flip servers (delta_report `sft_candidates`) are the
  highest-information examples for the next teacher relabel round. Axis flip
  rate per adapter version becomes a *stability metric* alongside the
  v2_1 acceptance bar: a candidate adapter that raises flip rate on an axis
  without evidence of real-world drift fails eval. Respect
  `schemas/risk_axis_mapping_v1.json` (auth_strength = 4 classes) verbatim.
- **Corpus improvement**: `corpus_signals` ranks registry sources by
  instability; a source whose servers flip at elevated rate has weak or
  mutating metadata -> canonicalisation target. Pairs with the 2026-07-18
  duplicate finding: 29.9% dup overhead, ~92% cross-source, family key is
  offline inference only. Materialising `canonical_family` at ingest is the
  companion directive; change events grouped by family will then separate
  "the project changed" from "we ingested a sibling row".
- **Inference correlation**: `co_flip` lift identifies axes that move
  together (e.g. maintainer_trust and supply_chain co-flips). High-lift pairs
  are candidates for (a) joint calibration checks in eval, (b) explicit
  cross-features at inference, (c) schema refinement proposals.

## Deliberately NOT in this PR

- No UI surface and no public API (THE LINE: nothing keyed/signed ships on
  data we have not accumulated yet). A `changed-since` API + SPA panel is a
  builder directive once >= 2 waves of events exist.
- No definition-change capture wiring (orphaned modules need their own
  review; different data source).
- No backfill of history that was never captured (cannot invent the past).

## Operational notes

- First data lands with the next delta rescore import after Fly deploy
  (migration applies via release_command alembic). Tower-side pipeline
  degrades gracefully until then via fail-open capture.
- Weekly cadence: run delta_report after the Tuesday 02:00 moat rescore;
  attach JSON to the run dir alongside rpt.json.
- Prune policy: score_change_events older than 12 months may be rolled up
  into score_change_runs-style aggregates (not needed before ~2027).
