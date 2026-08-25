# Merge audit — unattended merges to `main`, 2026-07-15 → 2026-08-24

Forensic review of the auto-merge window. Scope: dangling references of the
"declared but absent" class — imports, DB objects, endpoints, and file paths
that resolve to nothing and only fail when something exercises them.

Method: every claim below is verified against source, git history, or the live
write-service bus. `.md` files were treated as claims, not evidence.

---

## 0. Two corrections to the audit premise

Both change what the rest of this document can say, so they come first.

### 0.1 `main` is clean; the host build workspace is not

The audit was initially run against the checkout at `/home/workspace/zo_sentinel`.
That checkout is **26 commits behind `origin/main`** and carries a large volume of
untracked files that build daemons write into it. Findings taken from it do not
describe `main`.

Re-run against a clean export of `origin/main`, the live surface is healthy:

| Check (clean `origin/main`) | Result |
|---|---|
| `generate_spine.py --check` | PASS |
| `generate_spine.py --strict` | PASS — 32 services, 6 broken, all allow-listed |
| `tools/pull_check.py` (required gate) | PASS — schema drift 0 |
| `reachability_ratchet.py --enforce` | exit 0 |
| Import of all 32 spine mounts | 28 mounted, 4 no-router (all allow-listed), **0 import failures** |
| Import of all 24 modules under `app/` + `services/active/` | **1 failure** (§2.1, not on a live path) |

Everything in §1 below is measured against `origin/main`.

### 0.2 The Aug-13 `router_verdict` incident did not happen on `main`

The specific defect described — `from app.router_verdict import router` added to
`app/routers/__init__.py` without the module — is **not present in `main`'s history**:

- `git log --all -S'router_verdict'` matches exactly one commit, `d667541c`
  (2026-07-25), which lives only on an unmerged branch
  (`origin/auto/build/build_app_router_registry_collector-…`). It is not an
  ancestor of `origin/main`.
- `app/routers/__init__.py` has **never been committed** to `main`. On `main`,
  `app/routers/` is a PEP-420 namespace package holding three modules.
- `app/router_verdict.py` is likewise untracked; on the host it is a 9-line stub
  dated Aug 16.

What the evidence does support is a **local** outage in the build workspace, not a
merged one. Commit volume on `origin/main`: Aug 13 → 51, **Aug 14 → 0**,
**Aug 15 → 1**, Aug 16 → 80. So there was a real ~2-day stall, not ten days, and
it coincides with the untracked stub's Aug 16 timestamp. The breakage lived in
the working tree and never passed through a PR — which is also why no gate caught
it: **nothing gated it.** See §3.6.

---

## 1. Scope of the merge window

| Metric | Value |
|---|---|
| Merge commits (`--merges`) | **0** |
| Commits on `origin/main` since 2026-07-15 | **2,216** |
| Of those, PR squash-merges (`(#N)`) | **2,213** |
| Files changed vs. pre-window boundary | 2,285 |
| Lines | +205,223 / −1,215 |
| Authors | rob531 (2,207), dependabot (7), substrate-bot (2) |

The prescribed `git log --merges` returns nothing: auto-merge lands every PR as a
**squash commit**, so the window is 2,213 squashed PRs, not a merge list.
Boundary commit: `4020bba8` (2026-07-14).

### Highest-churn files

| Touches | File |
|---|---|
| **64** | `tools/reachability_deferred.json` |
| 19 | `tools/rescore/weekly_rescore.py` |
| 14 | `PRODUCT_SPEC.md` |
| 12 | `goose_runner.py` |
| 11 | `.github/workflows/pr-gates.yml` |
| 9 | `services/staged/__init__.py` |
| 8 | `.github/workflows/evaluator.yml` |

`tools/reachability_deferred.json` is the top-churn file by a factor of three, and
it is not a code file — it is the **deferral list** for the reachability gate.
64 touches in six weeks means the dominant repeated write in this window was
*declaring an orphan deferred rather than mounting it*. That file is now over its
own cap; see §3.4.

The insertion/deletion ratio (205k added, 1.2k removed) shows the window was
almost entirely additive — scaffolding accumulated, nothing was retired.

---

## 2. Findings

Ranked by blast radius.

### BROKEN NOW

Dangling references on a code path something actually exercises.

---

#### B1 — 7 FU acceptance predicates query tables that do not exist on the bus

**Where:** `tools/fu/fu_seed_predicates.py:94, 99, 104, 108, 112, 118, 122`
**Introduced:** `919a02f6` — *feat(fu): make FOLLOWUPS.md machine-closeable via acceptance predicates (#2182)*, 2026-07-28
**Reachable:** Yes, whenever FU closure is evaluated. Not imported by any module — it is run as tooling, so it does not break at import time.

Predicates FU-093, FU-058, FU-090, FU-054, FU-108, FU-104 and FU-001 issue SQL
against `server_scores`, `servers` and `score_runs` on the write-service bus
(`:8772/query`, named in the module docstring as "the documented read path").
Verified against `information_schema` on the live bus: the store holds 44 tables,
and **none of those three is among them**. Each query returns a catalog error.

Sibling predicates in the same dict (FU-115, FU-036, FU-107) query `service_health`
and `mesh_events`, which *do* exist — so this is not a wrong-database mistake
across the board, it is three table names that were never right.

**Blast radius:** These predicates are the mechanism that decides whether a
follow-up item can be closed. `sql_assert` carries a three-state contract
(GREEN / RED / **UNKNOWN**), and a catalog error lands in UNKNOWN, not RED. So
seven FU items are permanently un-closeable and report as *unknown* rather than
*failing* — the silent-hole shape, not a loud one. Governance reads a signal that
can never resolve.

**Suggested fix:** Point these three names at their real homes. `server_scores`,
`servers` and `score_runs` are app-plane tables (Postgres), not mesh-plane; the
repo's own builder recipe states app tables must not be read off `:8772` because
it holds a stale partial copy. Either re-target these predicates at the app
database, or map them to the mesh equivalents. Separately, `sql_assert` should
distinguish *catalog error* (a broken predicate — RED, needs a human) from
*genuinely unknown* (bus unreachable), so a nonexistent table can never masquerade
as UNKNOWN.

---

#### B2 — Untracked files in the host build workspace shadow the committed package

**Where:** `/home/workspace/zo_sentinel/app/routers/__init__.py` and `app/routers.py` — **both untracked**
**Introduced:** No commit. Never merged; not in `main`'s history.
**Reachable:** Yes — every local gate run, builder self-test and `import app.*` in that workspace.

On `main`, `app/routers/` is a namespace package. In the host workspace an
untracked `__init__.py` has been added to it, turning it into a regular package
that eagerly imports ~55 `app.router_*` modules, of which only two exist on disk.
That single file breaks `app.routers.media_assets`, and with it the spine mount
for `media_assets` and any local `import app.*`.

Measured locally: 1 of 32 spine mounts fails, and 100 unresolved import sites
appear under `app/` — **all of them in untracked files**. The same export from
`origin/main` has zero. An untracked `app/routers.py` also sits alongside the
directory, shadowed by it.

**Blast radius:** Contained to the build host — no ASGI server runs here and the
app deploys from `main`, so production is unaffected. But it is the workspace where
gates and builder self-tests execute, so local gate results from that tree are
untrustworthy, and it is the residue of the Aug 14–15 stall. The checkout being 26
commits behind compounds it.

**Suggested fix:** Remove the untracked `app/routers/__init__.py` and
`app/routers.py` from the host workspace and fast-forward the checkout to
`origin/main`. Then treat the underlying condition, which is the real finding:
the build workspace is a mutable tree that no gate observes and that diverges
silently from `main`. A periodic `git status --porcelain app/` assertion, or
building from a clean export, would have surfaced this on Aug 14 instead of Aug 25.

---

### LATENT

Dangling references on a path nothing currently exercises. These pass every gate
today and fail the moment their path is walked.

---

#### L1 — 52 staged services carry imports that do not resolve

**Where:** `services/staged/**` — 66 unresolved import sites across 52 files
**Introduced:** In-window; the dominant single source is the builder's scaffold lane
**Reachable:** No. Staged services are not mounted. They become reachable **on promotion to `services/active/`.**

Breakdown of what is being imported and does not exist:

| Sites | Missing module | Note |
|---|---|---|
| 15 | `app.dependency_overrides` | Not a module. `app` is the package; `dependency_overrides` is an attribute of the app instance. The correct form is `from app import dependency_overrides`. |
| 9 | `main` | Bare top-level `main`, mostly inside `__main__` self-test blocks |
| 3 | `app.cache` | Does not exist |
| 3 | `elasticsearch` | Undeclared dependency |
| 2 each | `sentinel_service.{api,models,utils}`, `fastapi.test_client`, `app.router`, `Levenshtein` | `fastapi.test_client` is a misspelling of `fastapi.testclient` |

Examples: `services/staged/admin_disputes/__init__.py:224`,
`services/staged/ask_query_expansion_v3/contract.py:62`,
`services/staged/axis_evidence/__init__.py:70`,
`services/staged/cadence_runtime_trend/router.py:147`.

The `app.dependency_overrides` error is the largest single cluster and is a known,
already-diagnosed builder failure mode — the goose recipe now warns about it in
prose ("There is NO app.dependency_overrides module"). Prose in the prompt has not
stopped it: 15 sites still carry it.

**Blast radius:** This is the Aug-13 *class*, pre-staged 52 times over. Promotion
is the trigger. `tools/promote_staged_to_active.py` runs `model_import_linter`,
which — verified — performs **no imports at all** (0 uses of `import_module` /
`__import__`); it is a narrow regex fixer for `Mcp*` class-name casing. So nothing
between authoring and mounting ever attempts the import. The catch happens later,
at spine mount time, via the one gate that has a fail-open hole (§3.1).

**Suggested fix:** Add an import test to the promotion path — attempt
`importlib.import_module(import_path)` on the post-move module and refuse the
promotion on failure. This is the cheapest possible placement: it is one import,
it runs on the ~1/day promotion event rather than on every PR, and it converts all
52 from latent to visible without touching the builder. For the
`app.dependency_overrides` cluster specifically, extend `model_import_linter` to
mechanically rewrite it to `from app import dependency_overrides` — the same
"hand-crafted linter beats a better prompt" argument that module was written for.

---

#### L2 — `app/scoring_consumer.py` cannot import under the pinned SQLAlchemy

**Where:** `app/scoring_consumer.py:4` — `from sqlalchemy.sql import Row`
**Introduced:** `c02fa135` — *build: app_scoring_consumer (#1711)*, 2026-07-25
**Reachable:** No. Nothing imports it; it is not in the spine.

`Row` is not exported from `sqlalchemy.sql` in SQLAlchemy 2.x. CI pins
`SQLAlchemy>=2.0.51,<2.1`, so this fails in CI's own environment, not merely
locally. Verified: `Row` is available as `sqlalchemy.Row` and
`sqlalchemy.engine.Row`, but not on `sqlalchemy.sql`.

This is the **only** import failure among all 24 modules under `app/` and
`services/active/` on clean `main`.

**Blast radius:** Low today — nothing reaches it. It is a live-path defect the
moment anything mounts or imports it, and it sits in `app/`, which no blocking
lint covers (§3.2).

**Suggested fix:** `from sqlalchemy import Row`. Then decide whether the module is
wanted at all — it has had exactly one commit since it was created a month ago and
has no callers.

---

#### L3 — Self-test block imports a module that does not resolve

**Where:** `services/active/cadence_job_sla_report/router.py:92` — `from main import app`
**Introduced:** `79d016e5` — *promote: first autonomous staged->active promotion (cadence_job_sla_report) (#3171)*, 2026-08-11
**Reachable:** No — the import sits inside `if __name__ == "__main__":`, so the service imports and mounts correctly.

This is the single unresolved import in `services/active/`, and it is inert at
mount time. It matters only because this file is the **first autonomously promoted
service** — the exemplar the promotion lane will copy. The defect propagating
matters more than the defect.

**Suggested fix:** Point the self-test at the real app (`from app.main import app`)
or build a local `FastAPI()` in the self-test, per the pattern the builder recipe
already prescribes.

---

### GATE GAPS

What the required checks do not catch. Required contexts on `main`:
`capmap-check`, `static-analysis`, `smoke-ladder`, `frontend`, `pytest`,
`no-hollow`, `schema-prm`.

---

#### G1 — The import gate and the spine gate each delegate `app.main` to the other

**This is the gap that most closely matches the incident class.**

`tests/ci/smoke_ladder.py:397-401` — `tier4_spine` wraps `import app.main` in a
`try`. On any exception it appends a **warning** and records the check as
**`True`**, with the note *"skipped: app.main import owned by tier1"*.

`tests/ci/hermetic_manifest.py` — tier1's allowlist is 33 modules. Verified
programmatically: **zero of them are under the `app` package.** tier1 does not own
`app.main`. Neither tier tests it.

Demonstrated, not inferred. Two defects were injected into separate clean exports
of `origin/main` and the tiers run against each:

| Injected defect | tier0 | tier1 | tier4 | Caught? |
|---|---|---|---|---|
| `app/routers/__init__.py` imports a nonexistent module (the Aug-13 shape) | pass | pass | **FAIL** | Yes — tier4 |
| `app/db.py` imports a nonexistent module (valid syntax; breaks `app.main`) | pass | **FAIL** | **pass (warning only)** | Only by accident |

The first case is caught because `media_assets` is a spine-mounted service that
transitively imports `app.routers`, so the failure surfaces as a mount failure
while `app.main` still imports — the fail-loud spine working as designed.

The second is the hole. `app.main` becomes unimportable, tier4 declines to judge
it, and the only reason CI goes red is **incidental**: two root-level modules in
tier1's allowlist (`verdict_breakdown_api`, `org_entity_search_api`) happen to
import `app.db` transitively. Coverage of the `app` package is therefore an
accident of which root modules the allowlist contains, not a property anything
asserts.

**Blast radius:** Measured. Of the 19 modules under `app/` on clean `main`, **8 are
imported by no required gate at all**:

```
app.api.axis_critical_servers_api        app.routers.ask_corpus_health_api
app.api.never_scored_burndown_api        app.routers.server_risk_tier_alert_router
app.api.server_axis_values_api           app.scoring_consumer
app.api.server_risk_tier_overview_api
app.api.verdict_export_api
```

Seven import cleanly today. The eighth is L2 — which is exactly why L2 reached
`main` and has sat there for a month.

**Suggested fix:** Delete the fail-open branch. If `app.main` does not import,
tier4 should FAIL, not warn — the comment above it already says a check that
silently degrades is how presence-not-correctness got here, and this is that check
degrading. Then add `app.main` to tier1 explicitly so ownership is real rather
than assumed. Both are one-line changes.

---

#### G2 — The blocking lint does not cover `app/` or `tools/`

**Where:** `.github/workflows/pr-gates.yml:296-298` (blocking) and `:300-304` (report-only)

`static-analysis` runs ruff `F,E9` as BLOCKING over exactly three paths:
`zo_sentinel`, `tests/ci`, `tests/gates`. The whole-repo pass immediately below it
runs with `--exit-zero` — report-only.

So `app/`, `tools/`, `services/**` and every root-level module have **no blocking
static analysis**. `zo_sentinel/` — the one tree that is gated — has, verified,
**zero** unresolved imports. The gated tree is clean and the ungated trees hold
every finding in this report. That correlation is the argument for widening it.

Note also that ruff would not have caught most of this even if widened: pyflakes
resolves names within a file, not module existence across the tree. It catches
L2's *class* of error only if the symbol is used undefined, not if the import
simply fails.

**Suggested fix:** Extend the blocking ruff invocation to `app/` and `tools/`
first — both are small and both are clean enough today to ratchet immediately.

---

#### G3 — The spine's strict gate is a file-existence check, not an import test

**Where:** `tools/generate_spine.py:170-172`

To answer the question posed directly: the required check performs **neither a
pure syntax test nor an import test**. `validate()` is documented in its own
docstring as *"Static validation of every active entry. No import — cannot
degrade."* It checks `os.path.isfile()` on the resolved module path and then does
a **regex scan** of the source for an `APIRouter` marker.

Consequences:

- It **would** catch a spine entry whose top-level module file is absent (`MISSING`).
- It **cannot** catch a module that exists but raises on import — including one
  importing a nonexistent module, which is the Aug-13 shape. `py_compile` and a
  regex both pass such a file.
- Runtime correctness is explicitly delegated to tier4 — which is G1.

The design is coherent: static presence here, runtime correctness there. The
failure is that the runtime half fails open.

**Related, and stale:** `tools/spine_known_issues.json` carries a `known_runtime`
entry asserting that `server_axis_scores_summary_router` fails to import on a
model-name casing error. Verified against clean `main`: it **imports successfully
and exposes a router**. The entry is stale. `--strict` staleness-checks the
`known` list but **not** `known_runtime`, so this entry cannot self-retire — and
while it stands it will silently absorb a genuine future failure of that service.

**Suggested fix:** Apply the same staleness rule to `known_runtime` that `known`
already has, which will fail the gate until this entry is removed.

---

#### G4 — The reachability gate prints its own reopen trigger and exits 0

**Where:** `tools/reachability_ratchet.py`, wired at `.github/workflows/pr-gates.yml:81`

Run against clean `origin/main`:

```
verdict: REGRESSION  (orphans=339 baseline=277 delta=+62)
  deferred (declared, unmounted): 63  -> effective=276 delta=-1
  DEFERRED LIST OVER CAP: 63 > 40. Per the 2026-07-21 CofC ruling this is a
  REOPEN TRIGGER -- the hatch has become the new graveyard. Escalate to the
  chairman; do not raise the cap to make this quiet.
```

`--enforce` **exits 0.** The gate passes because the effective delta after
deferrals is −1. The reopen trigger is printed as advisory text inside a passing
check, so it appears in no PR status and blocks nothing.

339 routers are declared and mount nowhere. 63 are formally deferred against a cap
of 40. And `tools/reachability_deferred.json` is the single highest-churn file of
the entire window at 64 touches (§1) — the deferral hatch is absorbing roughly one
write per day.

This is the answer to Phase 2c's second half — *registered routes no caller uses* —
at a scale of 339, and the mechanism that was built to make that visible is
currently reporting it into a passing gate.

**Blast radius:** Governance rather than runtime. Nothing is broken by an orphan;
what is broken is the instrument that was supposed to stop orphan growth. The
repo's own gate comments name this exact failure mode — *"an instrument reporting
faithfully into a place nobody reads."*

**Suggested fix — needs Robin.** The ruling that set the cap also says not to raise
it. Options are to make the over-cap condition exit non-zero, or to hold the queue
until the deferred list is triaged back under 40. Both are policy calls, not code
calls, and the ruling explicitly routes them to the chairman.

---

#### G5 — Nothing import-tests a service between authoring and mounting

Covered under L1. Stated here as a gap because it is structural, not per-item:
the builder scaffolds into `services/staged/`, the promoter moves the directory,
and the first thing that ever attempts the import is the spine at mount time.
`model_import_linter` does not import. 52 files are currently sitting on the far
side of that gap.

---

#### G6 — `:8772/query` silently truncates result sets

**Where:** write-service query endpoint, observed behaviour

The endpoint appends `LIMIT 200` to submitted SQL and returns the truncated rows
with a `count` reflecting **what was returned**, not what matched. A schema
enumeration against `information_schema.columns` returned 200 rows / 25 tables;
the true counts are **355 columns / 44 tables**. Nothing in the response indicates
truncation.

This is not a merge-window defect, but it is a live correctness hazard for exactly
the kind of DB-object verification Phase 2b calls for — any tool that reads schema
or row sets through the bus and does not paginate will silently reason about a
partial database. This audit hit it and had to page around it.

**Suggested fix:** Return an explicit `truncated: true` flag when the cap is
applied, so callers can detect it rather than having to know.

---

## 3. Phase 2 sweeps — coverage and results

| Sweep | Method | Result |
|---|---|---|
| **(a) Imports** | AST-extracted every `import` / `from … import` under `app/`, `zo_sentinel/`, `tools/`, `services/`, plus root modules; resolved each with `importlib.util.find_spec` (actual resolution, not pattern matching); then executed real imports for all 24 modules under `app/` + `services/active/` | 165 unresolved sites on clean `main`; **92 genuine** after removing sibling-resolvable script imports; 63 of those introduced in-window. `zo_sentinel/`: **0**. `app/`: 1 (L2). `services/active/`: 1, inert (L3). `services/staged/`: 66 (L1) |
| **(b) DB objects** | AST-extracted SQL string literals from `tools/` and `zo_sentinel/`, resolved table references against live `information_schema` via `:8772` (paginated — see G6) | 36 tables referenced in SQL. Most resolve to a different plane (app Postgres, `app_graph.db`, `gate_errors.db`) and are correct. **3 genuinely absent** on the bus they are queried against → B1 |
| **(c) Endpoints** | Ran the repo's own instruments — `pull_check.py`, `reachability_ratchet.py`, `app_surface_kl.py` | Schema drift **0** (required gate passes). 4 orphaned UIs, 3 gap areas — non-blocking. **339 orphaned routers**, 63 deferred over a cap of 40 → G4 |
| **(d) File paths** | AST-extracted absolute host paths from builder-layer code; checked existence on disk | 25 distinct paths, 23 present. The 2 absent are both in `tools/telemetry_capture_setup.py`, which is the **installer that creates them** — not defects. **Clean.** |

### How many of the window's merges would fail an import test today?

**63 files** introduced in the window carry an import that does not resolve today:
52 in `services/staged/` (L1), 2 in `tools/` (both false positives on inspection —
`fly_token` resolves via runtime `sys.path` insertion), 1 in `app/` (L2), 1 in
`services/active/` (L3, inert), and 7 root-level modules.

On the surface that actually runs, an executed import test over all 24 modules in
`app/` and `services/active/` produces **exactly one failure** — L2 — and it is not
on a live path.

**Scoping note:** the import test was deliberately *not* run across the ~900
root-level modules. `tests/ci/hermetic_manifest.py` documents why — most are host
daemons whose import has side effects (network calls, host-path reads and writes).
Executing them would have violated the read-only constraint of this audit. Those
modules were covered by static resolution only, which is why the figures above
separate "unresolved statically" from "fails an executed import".

---

## 4. Summary

The merge window did not put a broken import on `main`'s live path. The one live
import defect in the whole tree (L2) is unreachable, and the incident that
prompted this audit never entered `main` at all — it lived, and still lives, in an
unversioned build workspace that no gate observes (§0.2, B2).

What the window did produce is **52 staged services holding the same class of
defect**, waiting on promotion (L1), and a gate lattice where the runtime import
check fails open (G1), the blocking lint covers one tree out of five (G2), the
strict spine check is explicitly static (G3), and the orphan ratchet prints its own
escalation trigger inside a passing gate (G4).

The pattern across all four gaps is consistent, and it is the same one the repo's
own gate comments keep naming: the instruments exist and are largely correct. What
fails is where their output lands — a warning instead of a failure, a report-only
flag, an advisory line in a green check. The Aug-13 class is not un-gated here so
much as gated into a place nothing reads.

The single highest-value change is G1: two one-line edits — make tier4 fail on an
unimportable `app.main`, and add `app.main` to tier1 — close the mutual-delegation
hole and put the 8 currently ungated `app/` modules under a real assertion.

---

*Audit performed 2026-08-25 against `origin/main` @ `b66d3d84`. Read-only apart
from this file. The 117 open PRs were not touched.*

---

# REMEDIATION — 2026-08-25

Worked in the order the remediation brief set, one PR per phase so any phase can
be reverted independently. Every fix was verified by this audit's own method:
inject the defect into a clean export of `origin/main`, prove the gate now FAILS,
remove it, prove it passes. Both results are reported below; nothing is recorded
as "should catch it".

All verification ran against clean `git worktree` exports of `origin/main`
(`d9aaca9d`), never against the host build workspace — which is the point of B2.

## Status by finding

| Finding | PR | Verification | Status |
|---|---|---|---|
| **B1** — 7 un-closeable FU predicates | [#3990](https://github.com/rob531/zo-sentinel/pull/3990) | 7/7 UNKNOWN before → 7/7 RED with the probe fix alone → 4 RED / 3 GREEN / **0 UNKNOWN** with both. Unreachable bus still UNKNOWN. | **fixed** |
| **B2** — untracked files shadow the package | [#3985](https://github.com/rob531/zo-sentinel/pull/3985) | Workspace 31/32 mounts + 1 failure → **32/32 + 0**. Check: clean=0, +untracked=1, removed=0, rewound HEAD=1, restored=0. | **partial** — fast-forward blocked, [#3998](https://github.com/rob531/zo-sentinel/issues/3998) |
| **L1 / G5** — 52 staged services, no import test | [#3989](https://github.com/rob531/zo-sentinel/pull/3989) | Probe service: clean→PROMOTE, defect→HOLD, linter fix→PROMOTE; path-dependent defect→post-move FAIL + **rollback**. Sites 66→57. | **partial** — 25 still fail ([#4002](https://github.com/rob531/zo-sentinel/issues/4002)), 6 need a human ([#4001](https://github.com/rob531/zo-sentinel/issues/4001)) |
| **L2** — `sqlalchemy.sql.Row` | [#3991](https://github.com/rob531/zo-sentinel/pull/3991) | `ImportError` → imports OK. A second defect (FU-031 casing) sat behind the audit's one-line fix. | **fixed**; deletion proposed, [#3999](https://github.com/rob531/zo-sentinel/issues/3999) |
| **L3** — exemplar self-test import | [#3991](https://github.com/rob531/zo-sentinel/pull/3991) | `ModuleNotFoundError` → (import fixed) `TypeError` → rebuilt → **PASS** with real assertions. | **fixed** |
| **G1** — tier1/tier4 mutual delegation | [#3986](https://github.com/rob531/zo-sentinel/pull/3986) | Both audit defects. `app/db.py` shape: pre-fix tier4 **PASS** (warning only) → post-fix tier1 *and* tier4 **FAIL**, tier4 on a named check. | **fixed** |
| **G2** — blocking lint covers one tree | [#3992](https://github.com/rob531/zo-sentinel/pull/3992) | Undefined name in `app/` and in `tools/`: old gate exit **0** (misses), new gate exit **1** (catches). 20 pre-existing findings cleared. | **fixed** |
| **G3** — stale `known_runtime` | [#3993](https://github.com/rob531/zo-sentinel/pull/3993) | Stale entry → tier4 FAIL; removed → PASS; **live** suppression still suppresses; healed-but-kept → FAIL. | **fixed** |
| **G4** — ratchet prints its own trigger | [#3996](https://github.com/rob531/zo-sentinel/pull/3996) | 64th deferral: pre-fix exit **0**, post-fix exit **1**. Shrink → 0 + re-pin advice. | **partial** — triage [#4004](https://github.com/rob531/zo-sentinel/issues/4004), CofC ruling [#4005](https://github.com/rob531/zo-sentinel/issues/4005) |
| **G6** — silent truncation at `:8772` | [#3994](https://github.com/rob531/zo-sentinel/pull/3994) | Patch verified on the shipped method against a stub: 9.6M rows → `truncated=true`; caller's own LIMIT → `false`. | **needs Robin** — [#3997](https://github.com/rob531/zo-sentinel/issues/3997) |

All nine PRs are green: **zero failing checks** across every required context.

## Corrections to this audit

Four claims in the original document did not survive verification. Each is
recorded because the correction is itself a finding.

1. **§B1 — "these are app-plane tables (Postgres)".** `server_scores`, `servers`
   and `score_runs` exist on **no plane at all**: not among the 44 tables on the
   bus, not as a `__tablename__` in `app/models.py`, and in no migration or
   schema snapshot. There was nothing to re-target *to*, so they were mapped to
   the real tables (`mcp_server_registry`, `mcp_llm_axis_scores`, `agent_runs`).

2. **§G2 — "both are clean enough to ratchet today".** They were not.
   `app/` carried **10** F,E9 findings and `tools/` **10**, including a genuine
   `F821 Undefined name 'Session'` in the mounted router
   `app/routers/ask_corpus_health_api.py:35`. All 20 were cleared in #3992
   before the gate could be widened.

3. **§L2 — "`from sqlalchemy import Row`" as a complete fix.** It is necessary
   but not sufficient: behind it sat an FU-031 casing drift
   (`MCPLLMAxisScores` vs `McpLlmAxisScore`) that still blocked the import. The
   same pattern held for **L3**, where repairing the import exposed a self-test
   that could never have passed — it overrode `get_session` with a raw `sqlite3`
   connection while the router passes a SQLAlchemy `TextClause`.

4. **§G4 — "339 orphans" read as a service backlog.** The census scans
   **root-level `.py` only**; `services/staged/` is never walked. Of the 339,
   **288 are dead scaffolding** with no counterpart and no importer anywhere,
   and only **46** have a promotion path. The 930 staged services are invisible
   to this gate entirely.

A fifth correction was to my own work: the first revision of the G6 census
claimed the `code_nodes`/`code_edges` readers were unbounded. They are not —
every one sets an explicit `LIMIT`. Withdrawn and corrected in #3994; the
analyser now reconstructs whole SQL statements instead of judging concatenated
fragments separately.

## What the fixes have in common

The audit's own summary named the pattern — *"the instruments exist and are
largely correct; what fails is where their output lands"*. Every fix here is the
same move, and three of them were the identical bug in different clothes:

- **G1** recorded an unimportable `app.main` as a passing check.
- **B1** resolved a catalog error — a definite finding — into UNKNOWN.
- **G4** printed a documented reopen trigger inside a check that exited 0.

In each case the instrument was right and the verdict was discarded. The
recurring repair is to make "I could not evaluate this" distinguishable from
"this is fine", and to make the distinction *blocking*.

Two further observations from doing the work:

- **Every "trivial" fix had a second defect behind it.** L2, L3 and G2 all
  looked like one-liners and none was. A one-line fix that has never been
  executed is a hypothesis.
- **The deferral arithmetic rewarded deferring.** `effective = orphans −
  deferred` meant each new deferral *improved* the gate's headline delta.
  `reachability_deferred.json` being the highest-churn file of the entire window
  at 64 touches is what that incentive looks like from outside.

## Open items

| # | Item | Label |
|---|---|---|
| [#3997](https://github.com/rob531/zo-sentinel/issues/3997) | Apply the G6 truncation patch and restart `write_service` (`zo_mesh` has no remote) | `agent:code-zo`, `needs-decision` |
| [#3998](https://github.com/rob531/zo-sentinel/issues/3998) | Fast-forward the build workspace — 46 daemon files block it | `needs-decision` |
| [#3999](https://github.com/rob531/zo-sentinel/issues/3999) | `app/scoring_consumer.py` has no callers — delete? | `needs-decision` |
| [#4000](https://github.com/rob531/zo-sentinel/issues/4000) | `model_import_linter --fix` corrupts SQL table names in string literals | `agent:code-zo` |
| [#4001](https://github.com/rob531/zo-sentinel/issues/4001) | 6 `app.dependency_overrides` sites import a callable that exists nowhere | `agent:code-zo` |
| [#4002](https://github.com/rob531/zo-sentinel/issues/4002) | 25 staged services still fail a dry-run import | `agent:code-zo` |
| [#4003](https://github.com/rob531/zo-sentinel/issues/4003) | 16 schema enumerations + 583 row reads do not paginate | `agent:code-zo` |
| [#4004](https://github.com/rob531/zo-sentinel/issues/4004) | Triage the 63: 12 RETIRE need approval, 34 blocked on #4000 | `agent:code-zo`, `needs-decision` |
| [#4005](https://github.com/rob531/zo-sentinel/issues/4005) | Deferred list 63 vs the CofC cap of 40 — escalation still owed | `needs-decision` |

**#4000 is the one to fix first among the actionable items** — it blocks 34 of
the 63 triage decisions and can already fire automatically during promotion.

*Remediation performed 2026-08-25 against `origin/main` @ `d9aaca9d`. The 117
open PRs were not touched. `zo_sentinel_builder.py` was not edited (invariant 6).
All DB access went through `:8772`, paginated.*
