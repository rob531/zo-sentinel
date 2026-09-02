# RUN 1 — THE LEDGER RECKONING

**Date:** 2026-09-02 · **Role:** CEO, reading the whole ledger in one sitting ·
**Pack:** built 2026-09-02T21:50Z (fresh, < 24h — no rebuild needed) ·
**Ledger:** 375 entries, 168 open, 43 with no status field, ~211 actionable.

This run does not fix bugs. It decides **what is still true, what is still
worth doing, and what the next six weeks are for.** Four deliverables:

- `00_RECKONING.md` — this file: the judgment.
- `01_DISPOSITIONS.md` — machine-readable disposition for every actionable
  entry (instructions for the follow-up-triage lane).
- `02_LANE_VERDICT.md` — the verdict on the 36-directory fleet.
- `03_ROADMAP_6WK.md` — the six-week plan for mcprisky.io.

---

## The one-sentence finding

**The system solved counting and mistook it for the mission.** The registry is
at 250% of its row goal and 296,109 servers carry a score — but PLAN_200K's
actual goal was never rows; it was *"200,000 servers assessed with defensible
signals."* That number is **measured nowhere**, the corpus floor has been
frozen for 70 days, and as recently as late July 99.6% of the scored moat was
garbage. Counting is done. Defensibility is not even instrumented.

## What the numbers actually say (prod, pack build time)

| Metric | Value | Reading |
|---|---|---|
| registry_rows | 500,945 | **250% of the 200,000 goal** — the row target was met in July |
| scored_servers | 296,109 | 59.11% coverage of the inflated denominator |
| never_scored | 204,836 | **NOT a backlog** — arithmetic complement; ~186K are URL-duplicates + rows that never enter the scorer (FU-055) |
| newest_scored_at | 2026-08-31 (2d) | the recency band is fresh |
| oldest_scored_at | 2026-06-24 (**70d, frozen**) | the refresh half is **dead** — the tail cannot age (FU-361) |
| open PRs | ~282 | ~60 MERGEABLE, 222 UNKNOWN (lazily computed) — a scaffold flood, not a merge queue |

Two of these are the whole story. **never_scored is not work** — distinct-URL
coverage is ~99%, and the 59% headline is measuring the wrong denominator. And
**the frozen floor means a large share of "scored" servers were scored before
the adapter-attach fix (FU-093, ~2026-07-25)** and may still be garbage that no
refresh has revisited. The dashboard nonetheless calls 474,689 servers "scored"
when 191,273 carry `risk_tier=unassessed` (FU-269), and 99.5% of the corpus
that *is* scored sits in one or two risk tiers (FU-058) — the score carries
almost no information.

## The largest failure class is instrument failure, not product bugs

The 43 no-status entries are not a backlog — they are a **measurement failure**,
and they are an instance of the exact class that dominates this ledger. The
triage lane sweeps on `status:`; an entry that never got one is invisible to
it. The same shape recurs everywhere:

- **census taken from observations, not the world** (FU-374, FU-088, FU-269);
- **a guard blind to its own subject** (FU-289, FU-290, FU-341, FU-265);
- **a verify never seen red** (FU-249, FU-267, FU-268, FU-348);
- **a shared name is a shared counter** (FU-262, FU-295, FU-372);
- **existence mistaken for adoption** (FU-371, FU-254 — 15 of 88 tools consulted
  by nothing).

The system's most reliable output for three months has been *finding out that
the thing it built to measure X was not measuring X.* That is worth respecting:
it is why the corpus is honest. It is also the thing to spend the next six weeks
reducing.

## What this system should STOP doing

1. **Stop counting rows/scored as the scoreboard.** The row goal is met at 250%;
   `plan-200k-count-tracker` is tracking a solved problem. Repoint it (and the
   dashboard headline) at PLAN_200K §1's real bar: provenance + `ukey=sid` +
   7-axis-or-fail-visible-UNKNOWN + `fabricated=0`. (Roadmap R1.)
2. **Stop trusting the frozen-floor corpus as "assessed."** 70 days of the tail
   have not been revisited and may predate the adapter fix. Either re-verify or
   fail-visible them. (Roadmap R2/R3.)
3. **Stop racing discovery to 200K.** Registry is at 250% of goal; discovery's
   job is now to keep intake alive, not to grow (FU-054 is a maintenance item
   now, not the 200K gate it is billed as).
4. **Stop growing the lane count.** 16 of 36 directories are dead one-shots; 2
   more are spent campaigns. 60% of the "fleet" is clutter that inflates every
   adoption denominator and, worse, reads as live doctrine. Archive them.
5. **Stop answering honest-lane silence with new gates.** Trailing-window
   predicates punish the lanes that report truthfully (FU-347, FU-374, FU-284).
   Fix the comms; split censuses on calendar days; never add a gate.

## What it should START doing

Instrument defensibility (R1), unfreeze the floor without reaching for the dead
`--refresh-cap` cure (R2), retire the pre-fix garbage from the assessed count
(R3), make the headline honest (R4), and close the shopfront gap that a smaller
rival is already winning on (R5 — conduid). Fleet hygiene (R6) pays for itself.

## The bar for this run

A lane can produce a list. What a lane cannot produce is the judgment that **the
apparatus is pointed at the wrong number.** It is. The registry counter has been
green and climbing for six weeks while the product's defensibility — the only
thing a security analyst actually buys — has been unmeasured, frozen, and, for a
stretch, fabricated. The next six weeks are for making the number the system
reports be the number the mission needs.
