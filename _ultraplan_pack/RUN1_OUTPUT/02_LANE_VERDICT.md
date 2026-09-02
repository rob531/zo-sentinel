# RUN 1 — VERDICT ON THE LANE FLEET

Measured against `20_lanes.md` (built 2026-09-02T21:50Z). 36 lane
directories on disk; **20 registered, 18 enabled, 2 registered-but-disabled,
16 orphans.** Counting directories as "the fleet" overstates it by ~60%.

Two standing constraints obeyed throughout:
- **The prompt is ours; the schedule is not.** Every "retune" below is an edit
  to `SKILL.md` via `_tools/task_edit.py`, never a cron change and never a
  hand-edit.
- **When a lane looks silent, check the KIND of its predicate first.** Several
  lanes here are punished by a fleet-wide trailing window for reporting
  honestly (FU-347, FU-374, FU-284). Those are comms fixes, **not** new gates.

---

## A. The 16 orphans — ARCHIVE ALL 16 (extract doctrine first)

Every orphan is a finished one-shot or a dead watch keyed to a specific
July-2026 GPU run-id, a closed campaign wave, or (one case) a May-2026 SFT
eval. None is a live lane. The **sharp risk is not the clutter — it is reading
an orphan's SKILL.md as current doctrine**: several still carry standing
"PROVEN TECHNIQUE" prompts, `--may`/away-window instructions, and specific
instance-ids as if live.

| Orphan | What it was | Disposition |
|---|---|---|
| cadence-run3-reindex-check | verify ask-corpus reindex run 3 | ARCHIVE (event resolved 08-11) |
| campaign-wave-20260727-105859-landing-check | verify final moat-trust wave | ARCHIVE (campaign CLOSED 07-28) |
| canary-adapter-verify | FU-093 adapter canary | ARCHIVE (canary verdict delivered) |
| discovery-full-sweep-import | one-night 07-23 sweep import | ARCHIVE (daily lane subsumes it) |
| eval-watch-v2c | May-07 SFT eval pod watch | ARCHIVE (different subsystem, dead) |
| fu031-probe-eval | FU-031 self-test trend | ARCHIVE (observe-only, spent) |
| fu104-canary-watch | run 20260726 real-vs-garbage | ARCHIVE (self-disable mandate; verdict given) |
| fu104-monitor-run-20260726-014732 | one paid instance liveness | ARCHIVE (TERMINAL SUCCESS; self-delete) |
| reindex-run30-verify | reindex run 30 OOM check | ARCHIVE (spent) |
| rescore-20260730-001738-landing-check | never-scored wave landing | ARCHIVE — **but see doctrine #4 below** |
| rescore-overnight-shepherd | full re-score shepherd | ARCHIVE (superseded by moat-rescore-weekly) |
| score-45843424-wedge-check | one instance wedge guard | ARCHIVE (spent) |
| score-wave-check-2 | 07-24 wave status | ARCHIVE (spent) |
| score-wave-check-3 | 07-24 refired wave | ARCHIVE (spent) |
| score-wave-loading-check | 07-24 wave loading | ARCHIVE (spent) |
| vast-45168912-wedge-check | 07-17 instance wedge | ARCHIVE (spent) |

**Load-bearing doctrine to lift out before archiving** (most already lives in
`moat-rescore-weekly`; confirm, then delete the orphans):
1. **FU-106 scar** — never pass a tight `--deadline-min` override; it killed a
   healthy run.
2. **Row counts are not evidence; round-trip the label distribution back out of
   prod** for exactly the rows a wave wrote.
3. **The phase-qualified 9-minute rule** for GPU-0% wedge detection (base-model
   prefetch legitimately holds GPU at 0%).
4. **The small-cohort validity-gate finding** (rescore-20260730 landing-check):
   `score_validity` marks never-scored waves DEGENERATE on `maintainer_trust`
   for lack of on-ladder volume, so every Tuesday cadence spends ~$0.10–0.60
   and aborts. **This is a real open structural finding — file it as an FU
   before the orphan is deleted, not let it vanish with it.**

Retire the two DISABLED registered lanes as well:
- **mcprisky-200k-daily-repost** — campaign window ended 2026-07-22; FU-041
  records ~zero engagement and says do not renew. DELETE.
- **moat-rescore-first-run** — one-time first invocation, last run 2026-07-15.
  DELETE.

And **FU-171**: 15 spent one-shots still counted in adoption denominators.
Archiving the orphans *is* the fix; do it so every "N of M lanes adopted X"
denominator stops lying.

---

## B. The 18 enabled lanes — what each produces that nothing else does

Ordered by leverage. "Unique product" = what would be lost if it stopped.

### KEEP — load-bearing, no change
- **daily-chairman-review** (08:00) — the human-judgment proxy; the only lane
  that steers roadmap and triages PRs across the whole system. Highest leverage.
  This run is the quarterly version of what it does daily.
- **improvement-loop** (02/08/14/20) — the autopoietic fix engine: code selects
  evidence-ranked work, the fix lands in CODE, code verifies. The one lane that
  actually closes defects. Keep untouched.
- **follow-up-triage** (13:00) — the **only** writer of `status:` lines; the
  consumer of this run's disposition table. See retune note — its blind spot is
  the entire reason the 43 no-status entries exist.
- **moat-rescore-weekly** (Tue 02:00) — the scoring engine and watcher-of-the-
  watcher. Owns the fix for the frozen floor (FU-361). Keep.
- **deploy-runtime-from-main** (05:00) — the only thing keeping the runtime
  checkout from drifting behind main (FU-065). Keep.
- **graphify-kl-daily-refresh** (05:45) — builds the KL graph the architect
  steers on + reconciles per-FU code anchors. Unique. Keep.
- **cadence-jobs-daily-trigger** (06:20) — keeps perspective-snapshot and
  ask-corpus drift jobs firing. Keep.
- **vast-jobs-daily-audit** (07:30) — the cost-control lane ($25/mo budget,
  leak/wedge/spend). Cheap, unique, load-bearing. Keep.
- **goose-shadow-research** (Mon 07:30) — the only watch on upstream goose/AAIF.
  Weekly, low cost. Keep.
- **spark-scoring-run-review** (20:30) — audits the **Gemini Spark** corpus
  scorer (a second, deterministic-rubric scoring pipeline on Drive/Sheets,
  entirely separate from the Qwen moat scorer). Nothing else watches it, and it
  is the closest thing the system has to a *reproducible-rubric* scorer —
  directly relevant to the defensibility roadmap. Keep and watch this space.

### RETUNE — keep the schedule, fix the prompt
- **plan-200k-count-tracker** (07:45) — **the clearest "stop doing X" in the
  fleet.** It tracks registry/scored rows vs a 200K row target met in July
  (registry now 250% of goal). PLAN_200K §1 says the goal was never rows — it is
  *servers assessed with defensible signals*. Repoint this lane from row-count
  to the defensibility census (Roadmap R1). It is measuring a solved problem.
  (FU-055, FU-269.)
- **follow-up-triage** — extend its sweep to catch entries with **no** `status:`
  field. It keys on `status:` and is therefore structurally blind to the 43
  no-status entries. This is the fix that stops the headline defect of this run
  from recurring.
- **prod-drift-sentinel** (00:45/06:45/15:45/20:45) — keep (load-bearing deploy
  safety), but retire the residual "Phase 1 / STAGE-never-FIRE" prose labels its
  own header flags as stale. That stale-permission class already cost 20 stages
  / 452 commits of drift (FU-226, FU-229). `authority.json` is the only grant.
- **discovery-harvest-daily** (07:00) — keep firing (intake must never
  re-collapse, FU-054), but restate its mission: with the row goal met, its job
  is *maintenance of live intake*, not a race to 200K.
- **mcplookup-nightly-db-backup** (03:00) — keep, but it sits on a P1 cluster
  that says the backup is INFEASIBLE at current table size (~8h COPY, FU-107)
  and rides fragile/false-timeout proxies (FU-024, FU-081, FU-086). Retune the
  procedure, or the nightly "GREEN" is a false green.
- **clerk-signup-reconcile-nightly** (04:40) — a genuine negative control over
  the live webhook, but currently it has **no host it can run on** (FU-239:
  `tools/` not COPYed, Clerk key not on tower) while the webhook has delivered
  0 of 3 signups (FU-245). Right now the backfill masks the outage. Unblock the
  host, or it certifies a webhook that is dead.

### MERGE / consider-retire — remit largely covered elsewhere
- **zo-sentinel-pipeline-watch** (04:00) — advisory prod-health watch whose
  remit (Fly app, PG freshness, deploy bridge, CI, PR hygiene) is now largely
  covered by prod-drift-sentinel + vast-jobs-daily-audit + daily-chairman-review.
  It also skipped its own `lane_start` two days running (FU-287). MERGE its
  non-overlapping checks into prod-drift-sentinel and retire.
- **score-import-shepherd** (09:20) — its founding capability, the stranded-wave
  detector, exists only as prose and is re-implemented every run (FU-277,
  no-status; FU-331). It overlaps moat-rescore-weekly heavily. MERGE the durable
  stranded-wave scan into moat-rescore-weekly (as CODE, per the code-not-prose
  rule) and reduce or retire this lane.
- **autopoiesis-bar-tracker** (10:30) — its four-governance-scans-every-run is
  useful, but it is the single largest *source* of self-referential no-status
  defects (FU-313/314/315/338: reverts, positional selection, one-way latches —
  it keeps measuring its own product and mis-scoring it). Its own description
  concedes 52% of its honest cohort is absent from git, so no peer can review
  it. RETUNE hard, or fold the governance scans into improvement-loop's cycle.
  Do not read its prompt's dated self-instructions as current doctrine.

---

## C. Fleet-level findings (cross-cutting)

1. **The AWAY WINDOW boilerplate is expired but still live.** ~14 enabled
   prompts carry a verbatim "chairman off 2026-08-06..2026-08-30" block. Today
   is 2026-09-02; the window closed 08-31. Retire it from each prompt on its next
   self-edit — `authority.py --away` is the enforced value, the prose never was.

2. **Comms, not gates, is the recurring lane failure.** FU-347, FU-374, FU-284
   each punish an honest lane with a trailing-window or double-threshold
   predicate. Wire the obligation into a tool the lane cannot skip, split
   censuses on calendar days — never a new gate.

3. **Existence ≠ adoption (FU-371, FU-254).** A component is landed when a
   census can tell USED from UNUSED, not when it works. 15 of 88 tools are built,
   paid for, and consulted by nothing. Run a USED/UNUSED/NEVER-TOLD census
   before building more surface — it is a prerequisite to trusting any adoption
   number, including the ones in this verdict.
