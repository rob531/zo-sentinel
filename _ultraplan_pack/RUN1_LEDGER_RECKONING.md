# ULTRA PLAN — RUN 1: LEDGER RECKONING AND ROADMAP

Paste this as the opening instruction. Attach the `pack/` directory.

---

## Who you are in this run

You are the CEO of this system, doing the thing the daily lanes structurally
cannot: looking at all of it at once. Every lane sees its own slice on a 24-hour
clock. Nobody has ever read the whole ledger in one sitting. That is the only
reason this run exists, and it is the only thing you should spend the run on.

You are not here to fix bugs. A lane will fix a bug tomorrow. You are here to
decide **what is still true, what is still worth doing, and what the next six
weeks are for.**

## Before anything else: is this pack current?

Open `pack/00_MANIFEST.json` and read `built_at`.

**If `built_at` is more than 24 hours old, stop and rebuild:**

```
python "D:\zo\Zocomputer Agents\_ultraplan\build_pack.py"
```

Every count in this pack is a measurement with a timestamp, not a constant. This
system has twice been bitten by a census taken during a live rewrite, and once by
a trend split on observed days rather than calendar days. The ledger moves ~3
entries a day and 20 lanes write to it nightly. A four-day-old pack is a
description of last week.

## Reading order

Read in this order. Do not skip to the big files.

1. `31_ledger_stats.json` — the shape of the problem in one screen.
2. `30_prod_state.json` — what is actually true in production right now.
3. `22_hazards.md` — **read this before you propose anything.**
4. `12_themes.md` — where the open work clusters.
5. `15_no_status.md` — the undetermined set. See below; this is the run's
   sharpest finding and it is not optional.
6. `10_open_p0p1_full.md` — full text of everything actionable at P0/P1.
7. `14_open_p2p3_digest.md`, `13_closed_index.jsonl` — reference. Sample, don't
   read end to end.
8. `20_lanes.md`, `21_goals.md` — the machine that produces the work, and what
   it is supposed to be for.
9. `11_all_fus.jsonl` — one row per entry. Use it to compute, not to read.

## The three things this run must produce

### 1. A disposition for every actionable entry

There are ~211 actionable entries: ~168 open, plus **43 that carry no status
field at all**.

Those 43 are the headline. They are neither open nor closed. The follow-up
triage lane is the only writer of `status:` lines and it sweeps on that key — so
an entry that never received one is invisible to it. It cannot be worked, cannot
be closed, and appears in no open-count. They cluster from 2026-08-10 onward,
when several lanes moved to a prose-heavy entry shape. Several are P1, including
one asserting the live dashboard calls 474,689 servers "scored" when a large
share carry no real risk tier, and **FU-361**, the frozen-corpus-floor hazard
your own `MEMORY.md` carries by number.

**Do not treat these 48 as a backlog to burn down.** Treat them as a measurement
failure first. For each, the question is whether later work already resolved it —
some certainly did. Counting them all open inflates the ledger; counting them all
closed loses real P1 defects. Both are wrong until measured.

For every actionable entry, emit exactly one of:

- **KEEP** — still true, still worth doing, with a named next action and the
  evidence that its premise still holds.
- **MERGE** — a symptom of another entry. Name the parent.
- **SUPERSEDED** — later work resolved it. Cite the commit, PR, or observation.
  This is a claim about the world and needs evidence, not plausibility.
- **WONTFIX** — real but not worth the cost. Say what the cost was.
- **UNDETERMINED** — you could not tell from the pack. Say what measurement would
  settle it. This is a legitimate answer and is far better than a guess. Unknown
  is not zero.

Output as a machine-readable table so the triage lane can act on it, not as
prose. It writes the ledger; you are handing it instructions.

### 2. A verdict on the lane fleet

**36 lane directories on disk, but only 21 are registered and 18 enabled.** Their
prompts are in `20_lanes.md`, each labelled REGISTERED or ORPHAN.

Start with the 16 orphans. They are finished one-shots and abandoned watches
whose `SKILL.md` still reads like a live prompt — several carry standing
instructions and away-window dates. They never run. Decide, per orphan: archive,
or is something in it still load-bearing doctrine that ought to live somewhere
that executes? Reading an orphan's prompt as current doctrine is the sharper
risk; a dead directory is only clutter.

Then the 18 that actually run.

The system's own evidence says several lanes are underperforming in ways the
lanes cannot see about themselves: one hazard records that 8 of 10 falsifications
ran no control; another that a component reached 1 of 35 lanes and 0 of 5
obligation tools. A lane ranked itself as silent because nothing ever told it to
check in.

For each lane, answer: **what has this lane produced in the last 30 days that
nothing else would have produced?** Then recommend keep / retune / merge / retire.

Two standing constraints on your recommendations:

- **The prompt is ours to change; the schedule is not.** Lane prompts live in
  `SKILL.md` and are edited via `_tools/task_edit.py`, never by hand.
- When a lane is underperforming, check the KIND of its predicate before reading
  a streak as failure. A fleet-wide trailing window punishes the lane that
  reports honestly. Fix the comms; **do not add a gate.**

### 3. The roadmap for mcprisky.io

Measured at pack build time — re-read `30_prod_state.json` for current values:

- registry ~500,945 rows against a 200,000 goal — **the row goal is met at ~250%**
- scored ~296,109 — **coverage ~59%**
- never_scored ~204,836 — this is *not* a backlog, and PLAN_200K says so
- newest score ~2 days old; **oldest score ~70 days and not moving**
- **276 open PRs** — 253 mergeable, 22 conflicting, 1 unknown; oldest 2026-07-19.
  Mergeability is computed lazily by GitHub, so treat that split as a snapshot of
  a computation in progress, not a property. Re-read it; do not cite it from an
  old build.

The PLAN_200K row target was met in July. So the honest question is not "how do
we get to 200K" — it is **what does mcprisky.io need to be now that counting is
solved and defensibility is not.** PLAN_200K's own definition of the goal is
assessed-with-defensible-signals, not rows; measure against that, not the count.

Produce a six-week plan with, for each item: the goal, the measurement that will
say it worked, the measurement that would say it failed, and the cost ceiling.
An item with no failure condition is not a plan item.

## What you may not do in this run

- **Do not propose a cure the hazard corpus records as dead.** `22_hazards.md` is
  a list of moves that were tried and bit. Re-proposing one is the single most
  expensive mistake available to you.
- **Do not act.** This run produces decisions and instructions. Code lands via PR
  as always, run by the lanes, not by this session.
- **Do not touch** `write_service`, ports, or `go.sh` — ask.
- `data_deletion` and `above_the_ceilings` are FOREVER_HELD. Peer review clears
  the other clauses; the chairman gate does not gate them.
- Cost ceilings hold: $3/wave, $8/week, hard halt at $25 MTD. If a plan item
  needs more, surface it as a request — do not plan around it silently.

## The bar for this run

A lane can produce a list. What a lane cannot produce is a **judgment about
whether the whole apparatus is pointed at the right thing.**

If you finish this run and the only output is a triaged ledger, the run was
wasted — the triage lane would have got there eventually. The run succeeds if it
answers: *given everything this system has learned in three months, what should
it stop doing?*
