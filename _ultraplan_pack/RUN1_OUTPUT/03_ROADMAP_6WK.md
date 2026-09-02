# RUN 1 — SIX-WEEK ROADMAP FOR mcprisky.io

**Window:** 2026-09-02 → 2026-10-14 (aligns with PLAN_200K's M3 = 200K by
2026-10-15). Every item has a goal, a success measurement, a **failure**
measurement, and a cost ceiling. *An item with no failure condition is not a
plan item.*

**The framing.** PLAN_200K's row target was met in July (registry 500,945 =
250% of goal). Its actual §1 goal — *"200,000 servers assessed with defensible
signals: provenance-stamped source, verified identity `ukey=sid`, 7-axis
student scores OR explicit fail-visible UNKNOWN, and `fabricated=0`"* — is
measured nowhere. So the six weeks are not about counting. They are about making
the number the system reports be the number the mission needs.

**Cost discipline (hard, from the run charter):** $3/wave, $8/week, hard halt at
$25 MTD. Any item needing more is surfaced as a request, never planned around
silently. `data_deletion` and `above_the_ceilings` stay FOREVER_HELD.

**Dead cures NOT to re-propose** (the hazard corpus records these as tried-and-
bit): reaching for `--refresh-cap` to move the floor (instrument #4365 already
exists); treating `never_scored` as a backlog to burn down; adding a gate where
comms is the fix; re-baselining a stale gate into a level gate; the 277→335
move a target artifact forbids by name (FU-370).

---

## R1 — Instrument defensibility (build the real scoreboard) · **P0**

**Goal.** Compute PLAN_200K §1's four-part bar as one live number and repoint
`plan-200k-count-tracker` and the dashboard headline (FU-269) onto it. A server
counts only if: provenance-stamped source **and** `ukey=sid` verified identity
**and** 7-axis scores (or explicit fail-visible UNKNOWN) **and** passes the
`fabricated=0` audit.

- **Success:** the dashboard shows "N defensibly assessed" as a number distinct
  from registry rows and from `risk_tier=unassessed`, and N is reproducible
  within ±0.5% across two consecutive pack builds with no world change.
- **Failure:** the census cannot separate *assessed* from *catalogued/unassessed*,
  or N swings >5% build-to-build with no world change (that means it is still
  counting the wrong denominator, like the 474,689 headline it replaces).
- **Cost:** $0 (query + UI); lands via `improvement-loop`.
- **Depends on / closes:** FU-269, FU-055, and the reframe behind FU-361/FU-090.

## R2 — Unfreeze the corpus floor · **P0**

**Goal.** Diagnose why the refresh half never touches the tail — `oldest_scored_at`
has not moved from 2026-06-24 across ~385,000 refresh slots (FU-361). Fix the
cohort **selection** so the oldest rows age. **Do not** reach for `--refresh-cap`
(dead cure) or redefine the SLA to hide the freeze.

- **Success:** `oldest_scored_at` advances past 2026-06-24 within two weekly
  waves, and keeps advancing wave-over-wave.
- **Failure:** `oldest_scored_at` unchanged after two waves, OR a wave spends
  >$3 to move the floor <1 day (economically dead).
- **Cost:** $3/wave, $8/week ceiling; folds into `moat-rescore-weekly`.
- **Closes:** FU-361; unblocks the freshness half of R1.

## R3 — Retire the pre-adapter-fix garbage from the assessed count · **P1**

**Goal.** FU-093 established that 99.6% of the moat was garbage before the
adapter-attach fix (~2026-07-25); the fix shipped and a canary verified it, but
the frozen 70-day floor means many "scored" rows predate it and were never
revisited. Quantify how many current scores are dated before the fix commit and
either re-score them (via R2 waves) or mark them fail-visible UNKNOWN so they do
not count toward R1's N.

- **Success:** zero servers dated before the adapter-fix commit remain in the
  "defensibly assessed" count.
- **Failure:** pre-fix-dated scores still counted as assessed at the end of the
  window.
- **Cost:** folds into R2 waves (no separate spend).
- **Depends on:** R2 (needs the floor moving to re-score the tail).

## R4 — Make the headline honest · **P1**

**Goal.** Stop the dashboard calling unassessed rows "scored" (FU-269) and
surface the assessed / catalogued / unassessed split. Separately, investigate
the risk-tier degeneracy: 99.5% of the scored corpus is HIGH or CRITICAL
(FU-058), so the tier carries almost no information.

- **Success:** no dashboard card counts `risk_tier=unassessed` rows as "scored,"
  and the risk-tier distribution has at least two tiers each above 5% share.
- **Failure:** the headline still conflates unassessed with scored, OR the tier
  distribution stays ~100% one-or-two tiers after recalibration.
- **Cost:** $0 (UI + calibration probe); `improvement-loop`.
- **Closes:** FU-269, FU-058; supports FU-076.

## R5 — Close the shopfront gap a smaller rival is winning · **P1**

**Goal.** conduid.com is a direct MCP-trust-scoring rival with a 5×-smaller
corpus but a better shopfront, a two-sided flywheel, and — the one axis where it
is unambiguously ahead — search indexability (FU-075, FU-124). Ship (a) crawler
indexability (sitemap + server-rendered pages) and (b) the legible multi-axis
scorecard already in progress (FU-076), fed by the server-side signals it needs
(FU-080).

- **Success:** mcprisky.io server pages are indexed by Google (verified via
  `site:` query returning >0 assessment pages), and the scorecard renders ≥4
  deterministic signals per server.
- **Failure:** still 0 indexed assessment pages at the end of the window, OR the
  scorecard ships computing signals the app tier does not hold (FU-080 blocker
  unmet — a decorative scorecard).
- **Cost:** $0 (builder lanes; no paid compute).
- **Closes:** FU-124, FU-076, FU-080; advances FU-075/077/078; keeps FU-079
  (conduid watch) as standing intel.

## R6 — Fleet hygiene (pays for itself) · **P2**

**Goal.** Archive the 16 orphans + 2 disabled lanes + 15 spent one-shots
(FU-171); strip the expired 2026-08-06..08-30 away-window block from enabled
prompts; extend `follow-up-triage` to sweep entries that carry no `status:`
field (the fix for the 43 no-status entries and this run's headline defect).

- **Success:** `lane_dirs_on_disk` equals registered count; adoption denominators
  exclude spent probes; zero enabled prompts carry the expired away window;
  triage's next run assigns a status to every currently-no-status entry.
- **Failure:** orphans still on disk / still readable as live doctrine at the end
  of the window, OR a new no-status entry appears after the triage fix ships.
- **Cost:** $0; `follow-up-triage` + `daily-chairman-review`.
- **Closes:** FU-171, the no-status class (FU-262/277/…), FU-347/374 (comms).

---

## Sequencing

- **Weeks 1–2:** R1 (census) and R2 (floor diagnosis) in parallel — they are the
  two P0s and R3/R4 depend on them. R6 hygiene runs alongside (near-zero cost).
- **Weeks 2–4:** R3 (re-score the tail as the floor moves), R4 (honest headline
  + tier calibration).
- **Weeks 3–6:** R5 (shopfront + scorecard) — the outward-facing bet, unblocked
  once R1 gives it a defensible number to display.

**Kill criteria (from PLAN_200K §8, still binding):** junk-rate >10% on any
weekly sample freezes the offending lane; `fabricated>0` quarantines a lane until
root-caused; cost-ceiling breach is a hard halt. R1's census is what makes these
computable at all — which is why it is first.
