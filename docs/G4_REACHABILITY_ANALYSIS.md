# G4 — reachability: populations, lifecycle, and triage

Companion to `MERGE_AUDIT_2026-08-23.md` finding **G4**. The gate change is in
`tools/reachability_ratchet.py`; this document carries the analysis the finding
asked for.

The 2026-07-21 cap of 40 is treated here as what it is — an arbitrary threshold —
and is evaluated on merits in §2.

---

## 1. The populations are disjoint, which makes the raw count uninterpretable

**The census only scans root-level `.py` files.** `root_modules()`
(`tools/reachability_ratchet.py:123-128`) is `os.listdir(ROOT)` filtered to
`*.py`. `services/staged/` is never walked.

So the question "how many of the 339 orphans are staged services awaiting
promotion?" has a structural answer before it has a numeric one: **the two
populations barely intersect.** There are **930** directories under
`services/staged/` and **33** under `services/active/`, and the ratchet cannot
see any of them.

### The 339 root-module orphans

| Bucket | Count | Meaning |
|---|---:|---|
| `STAGED_COUNTERPART` | **46** | a `services/staged/<stem>/` exists — a real promotion path |
| `ACTIVE_COUNTERPART` | 0 | — |
| `HAS_IMPORTER` | 5 | something in the repo imports it, so a consumer exists |
| `DEAD_SCAFFOLD` | **288** | no staged/active counterpart and **no importer anywhere** |

### The 63 active deferrals

| Bucket | Count |
|---|---:|
| `STAGED_COUNTERPART` | **11** |
| `ACTIVE_COUNTERPART` | 0 |
| `HAS_IMPORTER` | 1 |
| `DEAD_SCAFFOLD` | **51** |

### The two figures the finding asked for

- **Legitimately awaiting promotion: 46 of 339** (11 of the 63 deferrals). These
  have a staged sibling and therefore a lane that ends in a mount.
- **Dead scaffolding with no plausible consumer: 288 of 339** (51 of the 63).
  Nothing imports them, no staged or active counterpart exists, and for the ones
  spot-checked the *only* reference anywhere in the repo is
  `orphanage/manifest.json` — a catalogue of orphans.

`DEAD_SCAFFOLD` is measured, not inferred: every `import X` / `from X import`
across all tracked `.py` was indexed and the module's own file excluded as a
self-reference.

**Consequence for the number:** "339 orphans" is 85% dead root-level scaffolding
and 14% duplicates of staged services. It is not a backlog of nearly-ready
services, and treating it as one would mis-price the work.

---

## 2. Why non-increasing, and why not the cap of 40

The cap was advisory. Being over it printed the CofC reopen trigger *inside a
check that exited 0*, so it appeared in no PR status and blocked nothing
(`tools/reachability_ratchet.py`, the `DEFERRED_REVIEW_CAP` branch).

Worse, the arithmetic **rewarded** deferring. `effective = orphans − deferred`,
so adding a deferral *lowers* the effective count and *improves* the headline
delta. Measured on the pre-fix ratchet with a 64th deferral injected: effective
delta moved `-1 → -2` and it still exited 0. The hatch was the cheapest way to
make the gate look better.

Holding to the cap of 40 as a blocking rule would fail every PR until 23
deferrals were triaged away — an unsatisfiable predicate for unrelated work, and
the same deadlock the deferred list was invented to avoid.

**Non-increasing is the rule that fits the evidence.** It stops the next write
without requiring the existing 63 to be triaged first, which is exactly the
reasoning that pinned `orphan_count` at its current level rather than an
aspirational one: gate the derivative, not the level. The cap's message is
retained as printed advice, because the ruling's escalation is still owed.

Pinned at the current **63**. It may only go down.

---

## 3. Lifecycle — when does each instrument actually run?

**Answer to the question posed: nothing that validates mountability runs before
or during directive generation. Every one of them is post-hoc on already-emitted
code.** The only thing consulted earlier is the code graph, and it is asked a
different question.

| Instrument | Runs at | Cite |
|---|---|---|
| `pull_check.py` | CI, on the PR — blocking | `.github/workflows/pr-gates.yml:41` |
| `reachability_ratchet.py --enforce` | CI, on the PR | `pr-gates.yml:81` |
| `generate_spine.py --check` | CI, on the PR | `pr-gates.yml:87` |
| `generate_spine.py --strict` | CI, on the PR | `pr-gates.yml:94` |
| `app_surface_kl.py --report` | CI, on the PR — **report-only** | `pr-gates.yml:103` |
| `spine_known_issues.json` | read by `--strict` and by tier4 | `tools/generate_spine.py:116`; `tests/ci/smoke_ladder.py:379` |
| `generate_spine.py --emit` | after an enforced promotion | `tools/promote_staged_to_active.py:288` |
| `code_nodes` / `code_edges` | **during directive generation** | `zo_sentinel/sentinel_directive_generator_goose.py:482-514` |
| `code_nodes` / `code_edges` | **during build** | `goose_runner.py:439-465`, called at `goose_runner.py:1990` |
| `is_mounted()` | at **publish**, post-emission | `zo_sentinel/publisher/auto_declare.py:67-84`, called `zo_sentinel/publisher/gitops.py:333` |

### The graph is already in the emission path — asked the wrong question

Two pre-emission consumers exist:

- `_existing_modules()` (`sentinel_directive_generator_goose.py:482-514`) queries
  `code_nodes` **during directive generation**, to dedupe: *"which module files
  already exist, so the architect proposes novel capabilities."*
- `_graph_context()` (`goose_runner.py:439-465`) queries `code_edges` **during
  the build**, to tell the builder *"existing code that depends on this file —
  do NOT break these contracts."*

Neither asks whether a mount point exists. The first asks *does this module
already exist*, the second *what depends on it*.

### `is_mounted()` — the closest existing thing, wired to the wrong outcome

`auto_declare.is_mounted()` (`auto_declare.py:67-84`) literally answers *"does
anything under `app/` reference this stem?"* — the mount question. But it runs at
**publish** time, after the file is written, and its only consequence is to
decide whether to append a deferral (`gitops.py:333`). It cannot influence what
was generated, and it never blocks. The module's own docstring is explicit:
*"The honest cost of this design is that the graveyard can still grow, just
visibly and with a paper trail."*

### Why `generate_spine.py --strict` passes while 339 orphans exist

Because the two sets are **disjoint by construction**. `--strict` validates
entries from `scan_active()` (`tools/generate_spine.py:132`), which reads
`services/active/*/service.toml` — and `validate()` (`:170`) iterates exactly
those entries.

Measured on clean `main`:

```
entries from services/active/    : 32
root .py files (orphan universe) : 1359
orphans that are ACTIVE entries  : 0
```

`--strict` asks *"is every declared mount valid?"*. It never asks *"is every
router declared?"*. An orphan is precisely a router that was never declared, so
it is invisible to that gate — correctly, since the reachability ratchet is the
instrument for the second question. The spine only validates **mounted entries**,
which is the premise the finding asked me to confirm. Confirmed.

---

## 4. Could the graph answer "does a mount point exist for this?" at emission time?

**Partly, and not as-is.** Report only; nothing here is implemented.

**What the graph can already answer.** `code_nodes` carries `source_file` and
`label`; `code_edges` carries `src`, `dst`, `relation` over
`calls / imports / imports_from / uses / inherits`. So *"is there an edge whose
`dst` is module X and whose `src.source_file` is under `app/`"* is a single join —
the same shape `_graph_context()` already runs. For an **existing** module the
graph answers the mount question directly, and more precisely than
`is_mounted()`'s regex-over-`app/` scan.

**Why that is not yet enough at emission time.** Three gaps:

1. **The module does not exist yet.** At generation time the artifact is a
   proposal, so no node exists and no edge can. The answerable question has to be
   inverted: not *"is X mounted?"* but *"does the mount point the directive names
   exist, and does it have room for X's declared prefix?"*. That requires the
   directive to **name a mount point**, which the directive schema does not
   currently carry.

2. **The graph is post-merge.** `tools/graph_refresh.py` rebuilds the index only
   when git HEAD differs from the graph's `built_at_commit`, on a 15-minute
   daemon poll, and it tracks the **deployed** repo. So the graph lags emission
   by at least a merge plus a refresh cycle. It is authoritative about what is
   merged, never about what is in flight — usable for *"does this mount point
   exist?"* (a fact about merged code), not for *"did this PR mount it?"*.

3. **The builder structurally cannot mount.** The `module_from_exemplar` lane
   guard forbids it and edit-class directives carry `output_file: null`. So a
   mountability check at emission time can only *refuse to generate* an
   unmountable directive; it cannot make the build mount anything. That is a
   change to what gets proposed, not to what gets built.

**What would need wiring** (design sketch, not a recommendation to build):

1. A `mount_point` field on the directive schema, populated by the architect.
2. A pre-generation predicate in `sentinel_directive_generator_goose.py`, beside
   the existing `_existing_modules()` call, that queries the graph for the named
   mount point and refuses the proposal when it resolves to nothing. This is the
   one place a check would run *before* emission.
3. A freshness guard on that query — `graph_refresh`'s `built_at_commit` versus
   the deployed HEAD — so a stale graph downgrades to "unknown" rather than
   silently answering "no mount point". Given B1 and G1, a mountability check
   that fails open would reproduce the exact class this audit is about.
4. `is_mounted()` at publish (`auto_declare.py:67`) could then become the
   confirmation of a decision already made, rather than the first time the
   question is asked.

**The honest summary:** the spineful-emission machinery the finding asks about
does exist, and the graph is already wired into the emission path — but every
instrument that judges *reachability* runs after the code is written, and the two
that run earlier are asking about novelty and about dependants. Nothing at any
stage asks whether the thing being built has somewhere to be mounted.

---

## 5. Triage of the 63 deferrals

Classified using the repo's own `tools/orphanage.py` (re-run fresh — the
committed `orphanage/manifest.json` was stale at 317 orphans against a census of
339), mapping its `why_unmounted` legend onto a verdict:

| `why_unmounted` | verdict | rationale |
|---|---|---|
| `MOUNTABLE` | MOUNT | clean and data-wired; the mount is the only missing step |
| `BROKEN_IMPORT` | MOUNT\* | unmountable only on model-name casing; mechanically repairable |
| `NO_ROUTES` | RETIRE | declares no routes, so it is not a service |
| `EDIT_CLASS` | RETIRE | `wire_`/`integrate_` name; never a service |
| `SUPERSEDED` / `SYNTAX_ERROR` | RETIRE | superseded, or does not parse |
| `UNKNOWN` | KEEP | needs human judgement |

Overridden to **RETIRE** where a `services/staged/<stem>/` also exists: the root
copy is a duplicate and the promotion lane is its real path.

**Tally: MOUNT 17, MOUNT\* 34, RETIRE 12, KEEP 0.**

> **MOUNT\*** depends on `model_import_linter --fix`. Before running it in bulk,
> note the defect recorded in PR #3989: its `Mcp*` casing autofix rewrites
> **SQL table names inside string literals and prose in docstrings**
> (`mcp_score_disputes` → `McpScoreDispute`). That must be fixed first, or the
> 34 repairs will corrupt queries.

**Robin approves any deletion.** Nothing here is deleted, mounted or retired.

| # | module | verdict | why unmounted | routes | data-wired | reason |
|---|---|---|---|---:|---|---|
| 1 | `api_key_manager` | **RETIRE** | MOUNTABLE | 2 | yes | clean and data-wired; the mount is the only missing step; ALSO duplicated at services/staged/api_key_manager, so the promotion lane is its real path |
| 2 | `audit_log_api` | **RETIRE** | NO_ROUTES | 0 | yes | declares no routes, so it is not a service; ALSO duplicated at services/staged/audit_log_api, so the promotion lane is its real path |
| 3 | `audit_log_export_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 4 | `audit_log_query_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 5 | `axis_change_attribution_probe` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 6 | `axis_score_variance_report` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 7 | `axis_scores_query_api` | **MOUNT*** | BROKEN_IMPORT | 3 | yes | unmountable only on model-name casing; mechanically repairable |
| 8 | `cve_detail_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 9 | `cve_facet_compile_service_enhanced_router` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 10 | `cve_facet_compile_wiring_v3` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 11 | `deferred_router_ledger_report` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/deferred_router_ledger_report, so the promotion lane is its real path |
| 12 | `deferred_router_triage_report` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/deferred_router_triage_report, so the promotion lane is its real path |
| 13 | `dispute_detail_api` | **MOUNT*** | BROKEN_IMPORT | 2 | yes | unmountable only on model-name casing; mechanically repairable |
| 14 | `entity_detail_view` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 15 | `never_scored_backlog_api` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/never_scored_backlog_api, so the promotion lane is its real path |
| 16 | `orphan_router_caller_probe` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 17 | `perspective_events_feed_api` | **MOUNT** | MOUNTABLE | 2 | yes | clean and data-wired; the mount is the only missing step |
| 18 | `propose_directive_outcome_log` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 19 | `registry_freshness_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 20 | `registry_growth_report_generator` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 21 | `registry_ingest_anomaly_report` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/registry_ingest_anomaly_report, so the promotion lane is its real path |
| 22 | `registry_source_freshness_report_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 23 | `registry_source_health_report` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 24 | `risk_tier_alert_router` | **MOUNT** | MOUNTABLE | 2 | yes | clean and data-wired; the mount is the only missing step |
| 25 | `risk_tier_by_source_report_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 26 | `risk_tier_change_dashboard` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 27 | `risk_tier_summary_dashboard` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/risk_tier_summary_dashboard, so the promotion lane is its real path |
| 28 | `risk_tier_summary_router` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 29 | `risk_tier_threshold_api` | **RETIRE** | NO_ROUTES | 0 | yes | declares no routes, so it is not a service |
| 30 | `risk_tier_threshold_calibration_probe` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/risk_tier_threshold_calibration_probe, so the promotion lane is its real path |
| 31 | `risk_tier_thresholds_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 32 | `risk_tier_trend_api` | **RETIRE** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step; ALSO duplicated at services/staged/risk_tier_trend_api, so the promotion lane is its real path |
| 33 | `score_dispute_router` | **MOUNT*** | BROKEN_IMPORT | 0 | yes | unmountable only on model-name casing; mechanically repairable |
| 34 | `score_results_push_consumer` | **MOUNT*** | BROKEN_IMPORT | 0 | yes | unmountable only on model-name casing; mechanically repairable |
| 35 | `scoring_axis_criteria_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 36 | `scoring_axis_summary_stats_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 37 | `scoring_consistency_audit_api` | **MOUNT*** | BROKEN_IMPORT | 2 | yes | unmountable only on model-name casing; mechanically repairable |
| 38 | `scoring_timeline_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 39 | `scoring_wave_summary_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 40 | `sentinel_external_api_router` | **MOUNT*** | BROKEN_IMPORT | 5 | yes | unmountable only on model-name casing; mechanically repairable |
| 41 | `server_axis_score_distribution_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 42 | `server_composite_risk_ranking_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 43 | `server_cve_exposure_summary_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 44 | `server_full_scorecard_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 45 | `server_pagination_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 46 | `server_perspective_change_timeline_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 47 | `server_registry_search_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 48 | `server_registry_source_distribution_view` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 49 | `server_risk_axes_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 50 | `server_risk_detail_api` | **RETIRE** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable; ALSO duplicated at services/staged/server_risk_detail_api, so the promotion lane is its real path |
| 51 | `server_scorecard_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 52 | `server_scoring_status_api` | **MOUNT*** | BROKEN_IMPORT | 2 | yes | unmountable only on model-name casing; mechanically repairable |
| 53 | `server_submission_api` | **RETIRE** | MOUNTABLE | 2 | yes | clean and data-wired; the mount is the only missing step; ALSO duplicated at services/staged/server_submission_api, so the promotion lane is its real path |
| 54 | `server_timeline_event_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 55 | `server_verdict_router` | **MOUNT** | MOUNTABLE | 3 | yes | clean and data-wired; the mount is the only missing step |
| 56 | `service_extraction_candidate_report_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 57 | `service_health_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 58 | `verdict_axis_detail_api` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 59 | `verdict_breakdown_router` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |
| 60 | `verdict_export_router` | **MOUNT*** | BROKEN_IMPORT | 0 | yes | unmountable only on model-name casing; mechanically repairable |
| 61 | `verdict_router_health_check` | **MOUNT*** | BROKEN_IMPORT | 1 | yes | unmountable only on model-name casing; mechanically repairable |
| 62 | `verdict_summary_api` | **MOUNT*** | BROKEN_IMPORT | 0 | yes | unmountable only on model-name casing; mechanically repairable |
| 63 | `verified_cves_api` | **MOUNT** | MOUNTABLE | 1 | yes | clean and data-wired; the mount is the only missing step |

**Tally:** MOUNT 17, MOUNT* 34, RETIRE 12

