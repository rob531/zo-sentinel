# DECISION: A population registry — every count names the set it counts

**Status:** PROPOSED
**Date:** 2026-09-02
**Deciders:** chairman, or peer review under the standing envelope
**Supersedes:** nothing · **Superseded by:** nothing

> **Pickup protocol.** This document is staged, not landed. If nothing engages
> with it, a one-time deadman escalates it (opens a PR and files a ledger entry)
> — see §Action items. To claim it, change `Status:` to `ACCEPTED` or
> `REJECTED`, or file a FU citing `DECISION_POPULATION_REGISTRY_2026_09_02`.
> Either stands the deadman down. It checks live state before firing; a decision
> already picked up will not be escalated.

---

## Context

The 2026-09-02 ledger reckoning (RUN1) and repo audit (RUN2) both concluded that
this system's most reliable output is *discovering that the instrument built to
measure X was not measuring X*. That is usually treated as many separate bugs.
It is one bug.

**Every counting failure on record is the same defect: the population being
counted was defined ad hoc, at read time, by whoever was counting.**

Measured instances, all from a single day:

| Count | What went wrong |
|---|---|
| `_tools` census | Four defensible denominators — 736 files, 472 executable, 130 live-relevant, 606 archival. All reconcile. None is written down anywhere, so next month's census picks differently and also reconciles. |
| `coverage_pct` 59.11% | Divides scored servers by registry rows, which include ~186K URL-duplicates that never enter the scorer. Wrong denominator, right arithmetic. |
| Dashboard "474,689 scored" | Counts 191,273 rows carrying `risk_tier=unassessed` (FU-269). |
| `dark_tools.py` | Counts a prose mention in a `docs/STATUS_*.md` as a caller. True dark is 10, reported 6. Merging a docs branch would flip three tools green on text alone. |
| No-status ledger entries | Measured 48, then 43, then 37 in one evening — three parsers, three populations, no definition. |
| FU-374 | Trend split on *observed* days rather than calendar days read a dormancy gap as a rise. |
| `plan-200k-count-tracker` | Tracks registry rows against a goal PLAN_200K §1 never stated in rows. |

The forces at play: ~20 lanes write counts nightly; no two share a definition;
prose conventions have repeatedly failed to bind (FU-255: *code steers, prose
does not*); and GOVERNANCE §2 already holds that *abstraction is not enforcement;
enumeration is* — but enumeration **in prose** is still prose.

**Crucially, this system has already solved this exact disease once.**
`zo_sentinel/schemas/loader.py` exists because silent *column-name* drift was
breaking every probe that hardcoded column names from memory. The cure was a
committed versioned snapshot, a fail-loud stdlib loader that works tower-side
and in CI, and a drift probe. Its own docstring names the goal: *"the recurring
failure mode this whole effort exists to prevent is silent column-name drift."*

Silent column drift and silent denominator drift are the same failure mode one
noun apart.

**Verified absent before proposing:** `git grep -l -iE
'denominator|population_id|cohort_def|assessed_population'` returns no
implementation — only the RUN1/RUN2 documents themselves. There is nothing to
reinvent here. (Separately noted: `agent_registry_extensions.py`, which standing
project instructions cite as "enum truth," is **not tracked in the repo** —
`git ls-files` returns nothing. Same class, one level up.)

## Decision

Add `schemas/populations_v1.json` — a versioned, committed registry in which
**every population this system counts is named, defined by an executable
predicate, and versioned** — plus a loader in the existing
`zo_sentinel.schemas` package and a census that reports uncited counts.

Thereafter: **a reported count cites a `population_id`, or it is not a metric.**

### Shape

```json
{
  "version": 1,
  "populations": {
    "tools.live": {
      "description": "Tool files that are candidates for being called. Excludes finished one-shots and retained probes; these are archive, not adoption denominator.",
      "universe": "_tools/**/*.py",
      "excludes": ["__pycache__/**", "*.bak*", "*.bak_*"],
      "predicate": "zo_sentinel.populations.tools:is_live",
      "last_count": 130,
      "counted_at": "2026-09-02T22:00:00Z",
      "notes": "Naive denominator 472 yields 25.8% adoption; the honest one yields 93.8%. Cite which."
    },
    "ledger.actionable": {
      "description": "FOLLOWUPS entries that are open OR carry no status field. Undetermined is NOT closed.",
      "universe": "FOLLOWUPS.md",
      "predicate": "zo_sentinel.populations.ledger:is_actionable",
      "last_count": 211
    },
    "servers.defensibly_assessed": {
      "description": "PLAN_200K §1's actual bar, the number the mission needs.",
      "universe": "mcp_server_registry",
      "predicate": "zo_sentinel.populations.servers:is_defensibly_assessed",
      "predicate_spec": "provenance_stamped AND ukey==sid AND (7_axis_scores OR fail_visible_unknown) AND fabricated==0",
      "last_count": null,
      "notes": "null because it has never been measured. Unknown is not zero."
    }
  }
}
```

Three populations first, deliberately: one from tooling, one from the ledger,
one from the product. If the mechanism cannot carry all three shapes it is the
wrong mechanism, and we find that out at three, not at thirty.

## Options considered

### Option A — Population registry (proposed)

| Dimension | Assessment |
|---|---|
| Complexity | Low — extends an existing package, stdlib only |
| Cost | $0; no paid compute |
| Scalability | Every future metric is one JSON entry |
| Team familiarity | **High — this is the `schemas/` pattern already in production** |

**Pros:** definitions become code, so they bind (FU-255); one place to argue
about a denominator instead of one argument per reader; subsumes roadmap R1,
which *is* "define the assessed population"; reuses a mechanism already trusted
against this exact failure mode; makes `dark_tools.py`'s prose-counting bug
expressible as a wrong predicate rather than an unnoticed one.

**Cons:** a registry nothing cites is itself a dark tool — the adoption risk is
real and is this project's most-recorded failure (*existence is not adoption*);
requires touching every counting site.

### Option B — Fix each counter in place

| Dimension | Assessment |
|---|---|
| Complexity | Low per fix, unbounded in aggregate |
| Cost | $0 |
| Scalability | None — the seventh instance costs what the first did |

**Pros:** each fix is small, independently reviewable, no new abstraction.
**Cons:** does not stop the eighth instance. This is what the last three months
did; the ledger records the result. It also leaves each fix's denominator
undocumented, so the fix itself is unverifiable later.

### Option C — Blocking CI gate: refuse any count without a `population_id`

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Cost | $0 direct; high friction cost |
| Scalability | Good on paper |

**Pros:** guarantees adoption.
**Cons:** **directly contradicts a standing finding** — trailing-window and
blanket gates punish the lanes that report honestly (FU-347/374), and the
recorded rule is *fix the comms, never add a gate*. It would also land into a
queue where 180 of 285 PRs are already stuck behind contexts that never fire;
adding a ninth required context to that is actively harmful.

### Option D — Do nothing; accept ad-hoc denominators

**Pros:** zero cost today.
**Cons:** every number this system reports remains unfalsifiable, which makes
autopoiesis impossible in principle: a system that cannot sense its own state
truthfully cannot maintain itself. It also means roadmap R1 has no foundation —
R1's success criterion ("N reproducible within ±0.5% across two builds") is
literally a population-stability claim.

## Trade-off analysis

The real contest is **A vs C**, and it is about enforcement, not design. Both
put definitions in code; they differ on whether adoption is compelled or made
easy. C guarantees adoption and violates a standing, evidence-backed rule
against gates. A respects that rule and accepts adoption risk.

A manages that risk with a **census, not a gate**: a report listing every count
emitted without a `population_id`, surfaced to the lane that emitted it. That is
the shape the record says works — FU-371 established that the fix for a
component reaching 1 of 35 lanes was *comms*, and explicitly not a gate.

B is what we have been doing. D is a decision to stop trying to measure.

## Consequences

**Easier:** arguing about a denominator once, in one file, with a diff.
Reproducing any published number. Writing R1 — it becomes one registry entry
plus a predicate, not a bespoke instrument. Detecting drift: a population whose
count moves without a world change is now a visible event rather than a
disagreement between two readers.

**Harder:** emitting a quick number. That friction is the point, but it is real
and it will be felt by lanes that currently just print a count.

**To revisit:** whether the census stays advisory. If adoption stalls at, say,
under half the counting sites after four weeks, the gate question reopens — but
it reopens with evidence, which is the only condition under which this project's
own record permits adding one.

**Explicitly NOT decided here:** any change to `dark_tools.py`'s caller test,
any archival of `_tools` files, and the `pr_regate.py` question. Those are
separate decisions that this one makes *expressible*, not resolved.

## Risks and how they are guarded

- **The registry becomes a dark tool.** Guard: the census, plus wiring the first
  three populations into their real call sites **in the same commit** — a cure
  wired into one door of eight reads as a cure (FU-343).
- **A predicate crashes and forges a clean pass for the rest.** Guard: the census
  runs each predicate in a **subprocess**, never an inline import (FU-290). The
  loader may import them; the thing that *grades* them may not.
- **The snapshot goes stale and is trusted anyway.** Guard: mirror the existing
  loader's `STALE_AFTER_DAYS` fail-loud behaviour. A missing or stale registry
  raises; it never returns an empty population, because an empty population
  reads exactly like a true zero.
- **`last_count` gets carried instead of measured.** Guard: `last_count` is
  advisory only and always paired with `counted_at`; consumers compute, never
  read it. `null` is a legitimate value meaning never measured — unknown is not
  zero.

## Action items

1. [ ] Land `schemas/populations_v1.json` with the three populations above.
2. [ ] Add `zo_sentinel/populations/` with the three predicates, stdlib-only,
       importable from tower, CI and a Windows workstation.
3. [ ] Extend `zo_sentinel/schemas/loader.py` with `load_populations()`,
       fail-loud on unknown id and on staleness.
4. [ ] Add the row to `schemas/README.md`'s consumer table.
5. [ ] Write the uncited-count census; run it **before** the change and require
       it RED, then again after. No test seam, no control by construction.
6. [ ] Wire all three populations into their existing call sites in the same
       commit as (1)–(3).
7. [ ] Predict the collected-test count change before pushing; a green whose
       count did not move ran none of the new tests.
8. [ ] **Deadman:** if this ADR is neither accepted nor rejected nor cited in the
       ledger by **2026-09-09**, escalate automatically — open a PR and file a
       FU so the triage lane routes it. Rationale: this project's recorded
       failure mode is not bad decisions, it is decisions nobody executes
       (cycle-0048 sat SELECTED for 10 days; an unwired cure went dark for 27).

**Cost:** $0. No paid compute, no GPU, no wave. Within all standing ceilings.
