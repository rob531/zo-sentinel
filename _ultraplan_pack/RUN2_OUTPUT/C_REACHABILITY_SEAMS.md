# RUN 2 / C — REACHABILITY + TEST SEAMS

**Measured 2026-09-02.** Clean clone `D:\zo\_ultraplan_run\zo-sentinel`, branch
`ultraplan/pack-20260902`, HEAD `0e92b5b34bb5fc0db9884a1054e8149ca783542a` (2026-09-02),
code == origin/main. Tower-side tools `D:\zo\Zocomputer Agents\_tools`. Live lane prompt
store `C:\Users\robin\OneDrive\Documents\Claude\Scheduled\<taskId>\SKILL.md` (36 dirs,
`dir /s /b SKILL.md | find /c /v ""` → **36**).

Every row below carries a path+line or a command+output. Where I could not measure, the
row says `COULD_NOT_DETERMINE` and names the measurement that settles it. **Unknown is not
zero.**

---

## 0 — HEADLINE

| verdict | n | items |
|---|---|---|
| REACHABLE-AND-WIRED (every door, adopted) | **4** | FU-268, FU-359, FU-265, FU-377 |
| ONE-DOOR (reachable, wired at a subset) | **3** | FU-343 (6 of 10, RED today), FU-309 (active only; 77 staged dirs ungated), FU-306/318 (`--pysrc` named in 3 of 36 prompts) |
| NEVER-TOLD / UNUSED (present, reachable, nothing invokes it) | **3** | `_tools/unblock.py` (0 of 36 prompts), `_tools/tower_path_doors.py` (0 callers anywhere), the score ledger named directly (3 of 36; 16 of 36 reach it only via `lane_start.py`) |
| NOT BUILT (no cure exists to be reachable) | **3** | R1's four-part bar, R2's `oldest_scored_at` mover, R5 indexability |
| LIVE DEFECT unchanged | **1** | FU-251 (Windows-MCP 23 of 36 prompts vs Desktop Commander 3 of 36) |

**FU-359 answer: FALSIFIED — the claim is no longer true.** The required `pytest` check
now collects all five `tests/test_rescore_*.py` files plus `tests/test_vast_spend_selftest.py`,
58 tests, and they load the real GPU harness `tools/rescore/weekly_rescore.py` by file path.
Full evidence in §3.

**Collected-test baseline, verified: 661.** Any future green on the required `pytest` check
that reports a number other than 661 without a corresponding allowlist/test change ran a
different suite than this report measured.

---

## 1 — JOB 1: REACHABILITY OF THE KEEPS

Question is never "is the fix correct". It is: (1) can it be invoked from the surface that
was bitten, (2) is it wired at **every** door of its shape, (3) is it adopted or merely
present.

### 1.1 REACHABLE-AND-WIRED

#### FU-268 — `record_credit(state=...)` without `path=` must not clobber · KEEP (§1, P1)
- **Cure location:** `D:\zo\_ultraplan_run\zo-sentinel\tools\ops_audit_state.py`.
  `_in_memory()` at line 110-135; `return state is not None and path is None` at line 135.
- **Wired at every door of its shape: YES, 2 of 2.** `record()` line 152
  (`if path is not False and not in_memory: save(state, path)`) and `record_credit()`
  line 199 — the identical guard, via the shared helper rather than a copy.
- **Adopted: USED.** `tests/test_ops_audit_state.py` is in the required `pytest` allowlist
  (`.github/workflows/evaluator.yml`), so the assertion blocks a merge.
- **Deviation to flag.** Run 1's disposition asked for a **REFUSAL**. What shipped is a
  silent in-memory no-op (docstring line 143: *"IN-MEMORY and writes nothing"*). A caller
  who believes it persisted gets rc=0 and no file — the FU-313 shape (`a verify that
  survives success cannot witness failure to act`). Verdict: destructive default is closed;
  the refusal Run 1 asked for is not what landed.

#### FU-265 — did any code path ever read `PEER_CLEARABLE`? · §1 UNDETERMINED
- **Measurement Run 1 asked for, run:** `grep -n PEER_CLEARABLE _tools/authority.py`.
- **Answer: YES.** `_tools/authority.py:537` — `if disp == "PEER_CLEARABLE":`, and it is
  evaluated **before** the away test (comment at :532). The vocabulary is defined at
  `_tools/peer_review.py:137` and enforced at `peer_review.py:514`
  (`if clause not in PEER_CLEARABLE:`).
- **Negative controls exist in-tree:** `authority.py:891/895/920` assert
  GRANTED for CLEARED, GRANTED for ACTED, and permanence across the away window closing.
- **Disposition:** FU-265 is resolvable. The value is readable; the 08-06 unparseable-value
  bite is closed.

#### FU-377 — `friction.self_detach()` stranded in 2 tools of 3 · KEEP (§1)
- **Cure reachable from the bitten surface: YES.** `dark_tools.py` main() rebuilds a child
  argv from **parsed** args and calls `_fr.self_detach(child, f"dark{os.getpid()}", a.detach_wait)`
  at `_tools/dark_tools.py:885`. Detach is the default (`detach = (not (a.self_test or
  a.assert_wired)) if a.detach is None else a.detach`, line 870), so a lane that types the
  bare command gets the cure without knowing it exists — the correct shape.
- **Census in the SAME change: YES, 3 of 3.** Lines 829-871 enumerate every live caller and
  say why two stay foreground (`improve_loop.FLOOR --self-test` and
  `improve_loop.select() --assert-wired` are rc-GRADED 0/1/2; a detached rc=3 would corrupt
  a verdict, not fix an orphan) and that `lane_start._dark_step()` was updated to pass
  `--no-detach` in the same commit.
- **Negative control named and observed RED:** `_tools/probe_darktools_selfdetach_20260902.py`,
  "observed RED on poles A and E before this change (rc=1) with B, C1 and C2 green".
- **Measured, not asserted:** 282.0s bare report path vs a 55-90s transport cut
  (`_friction_scratch/dt_timing_20260902.json`).
- **RESIDUAL ONE-DOOR RISK (new finding).** The population the guard can ever fire on is a
  hard-coded name list: `_tools/friction.py:682` —
  `_SLOW_FLEET_TOOL = re.compile(r"\b(lane_start|dark_tools|improve_loop)\.py\b", re.I)`.
  That is an enumeration of **names**, not a measured runtime class, over 659 files in
  `_tools`. This is the FU-326 shape ("the dam fix enumerated failure NAMES; the next wave
  returned an unlisted name"). Nothing in `_tools` measures any other tool's wall-clock.
  **Measurement that settles it:** time every `_tools` entry point that a lane prompt names
  (`--self-test` or `--help` is not the path; the report path is) and compare against 55s.

#### FU-359 — see §3. REACHABLE-AND-WIRED, closed 2026-09-01, independently re-verified today.

### 1.2 ONE-DOOR — reachable, wired at a subset of its shape

#### FU-343 — the tower-invisible classifier at every path-reading door · Run 1 marked `SUPERSEDED?` ("wired into all 9 tower-side doors")
**FALSIFIED. It is 6 of 10 today and the project's own census tool says RED.**

Command (Windows-side, read-only static scan):
```
python -u "D:\zo\Zocomputer Agents\_tools\tower_path_doors.py" --check
```
Output, 2026-09-02, rc=**1**:
```
doors that read a LANE-SUPPLIED path: 10   (excluding the classifier itself)
  consult friction.tower_invisible : 6
  DO NOT                           : 4

UNGUARDED DOORS -- each can publish ABSENT for a path this host merely cannot resolve:
    fu_verify.py                             --negative-control
    migration_content_class.py               --file
    record_prod_fire.py                      --count-attempts
    scheduler_mirror.py                      --path
VERDICT: RED
```
- **Why this one matters most.** `fu_verify.py` is named in **14 of 36** live lane prompts
  (`findstr /s /m /i /c:"fu_verify.py" SKILL.md | find /c /v ""` → 14). Its
  `--negative-control` door is unguarded, so a control file written into a scratchpad the
  tower cannot resolve is published as ABSENT rather than INVISIBLE — a control that reads
  as "no such file" is a control that silently did not run. That is the exact
  `is_file()==False = absent OR invisible` hazard, sitting on the tool 14 lanes use to
  close follow-ups.
- **`scheduler_mirror.py --path`** is unguarded and is the tool that would tell you the
  scheduler is dormant (FU-350/FU-378 class).
- Do **not** close FU-343 on the "all 9 doors" note. The door count is 10 and the census is
  the thing that says so.

#### `_tools/tower_path_doors.py` itself — UNUSED, and this is the second half of FU-343
- `grep -rln "tower_path_doors" --include="*.py" _tools` → **only itself**.
- `findstr /s /m /i /c:"tower_path_doors" SKILL.md` over the 36 live prompts → **0**.
- So the instrument that produces the RED verdict above has had **no reader since it was
  promoted on 2026-08-13**. It is a correct, self-testing, exit-coded census with a
  `--self-test` negative control, reporting faithfully into a place nobody reads — the
  class the repo's own `pr-gates.yml` comment names as "the same shape as every expensive
  failure here".
- **Fix is comms, not a gate.** Name `tower_path_doors.py --check` in the lane that already
  owns hazard hygiene (`improvement-loop`, the only prompt that names `improve_loop.py`).

#### FU-309 — "no PR changes `services/active/<n>/` without a `service.toml`" · KEEP (§1)
- **The assertion EXISTS and is BLOCKING for `active/`.** `tools/generate_spine.py`
  assigns `status = "NO_TOML"` (line 187-188) for any `services/active/<n>/` dir with no
  manifest; `--strict` exits 1 on any `unlisted_broken` entry (lines 380-385); pr-gates
  runs `python tools/generate_spine.py --strict` as a BLOCKING step in the `capmap-check`
  job (`.github/workflows/pr-gates.yml`, "Spine strict validation").
- **Measured on this clone:**
  `services/active` → **32 dirs, 0 missing service.toml**. (32 also matches the "32 mounted
  in prod" figure in the moat baseline — corroboration, not coincidence.)
- **THE OTHER DOOR IS OPEN.** `services/staged` → **1,133 dirs, 77 missing `service.toml`**.
  `generate_spine.scan_active()` walks `ACTIVE_DIR` only (line 133-135).
  `tools/check_service_manifests.py` uses `MANIFEST_GLOB = "services/*/*/service.toml"`
  (line 64) — it validates manifests that **exist**, so a staged dir with no manifest
  produces nothing to check and the gate passes **vacuously**. The only thing that refuses
  is `tools/promote_staged_to_active.py:277` ("service.toml missing/invalid"), which fires
  at **promotion** time, weeks after the PR that authored the directory.
- **Verdict: cure wired at 1 of 2 doors.** FU-309's bite surface is the PR, and 77 staged
  services are already born unpromotable with nothing on a PR saying so. This is the same
  arithmetic as "staged 1,229 → active 464 → 32 mounted": the attrition has a measurable
  gate-shaped hole in it.
- **Predicted seam:** extend `tools/check_service_manifests.py` to assert *presence* per
  changed `services/*/<n>/` directory, with the 77 pre-existing dirs allow-listed by name
  the way `spine_known_issues.json` allow-lists inherited spine debt (satisfiable gate on
  the DERIVATIVE — the pattern this repo already uses twice and that avoids the FU-256 dam).

#### FU-306 / FU-318 — is the `--pysrc` cure reachable from the PowerShell surface that was bitten?
- **Reachable: YES.** `_tools/friction.py:3405` — `ap.add_argument("--pysrc", ...)`, i.e. a
  real CLI door, not a Python-only function. Siblings `--run` (:3398), `--spawn` (:3384),
  `--poll` (:3388), `--grep` (:3420), `--detach` (:3412) are likewise CLI. `MAX_CLI_WAIT_S = 25`
  at `friction.py:99` confirms the FU-335 clamp landed.
- **Adopted: PARTIALLY.** Over the 36 live prompts:
  `friction.py` **16**, `--pysrc` **3**, `--run` **3**, `--detach` **2**, `self_detach` **0**.
- **Reading:** the cure is reachable and half the fleet is told the tool exists, but only
  3 of 36 prompts name the specific form that survives the bite. This is not a code defect;
  it is the NEVER-TOLD half of FU-371 applied to friction's own CLI.

### 1.3 NEVER-TOLD (comms defect — the fix is comms, never a new gate)

| tool | reachable? | named in live prompts | verdict |
|---|---|---|---|
| `_tools/unblock.py` (`--for <symptom>`) | yes, CLI | **0 of 36** | NEVER-TOLD. The "when blocked, ask what you already hold" cure is documented only in the operator's own memory index, which is a different surface from the SKILL.md a scheduled lane reads. |
| `_tools/tower_path_doors.py` | yes, CLI, exit-coded | **0 of 36**, 0 code callers | UNUSED (see above) |
| `_tools/roster_refresh.py` (FU-376) | yes | **0 of 36** | COULD_NOT_DETERMINE whether a *tool* calls it; not named in any prompt. Measurement: `grep -rln roster_refresh _tools --include=*.py`. |
| score ledger (`autop_score.py`, FU-371) | yes | **3 of 36** direct | Reaches **16 of 36** indirectly: `lane_start.py` imports it and `lane_start.py` is named in 16 prompts. FU-371's recorded "1 of 35 lanes" is **stale — recount before acting on it**. |
| `dark_tools.py` | yes | **1 of 36** | Present, barely told. |

### 1.4 LIVE DEFECT, unchanged

#### FU-251 — 23 of 34 lanes drive the desktop via Windows-MCP (records silent failures)
Re-measured over the 36 live prompts today:

| string | prompts naming it |
|---|---|
| `Windows-MCP` | **23** |
| `Desktop Commander` / `Desktop_Commander` | **3** / **1** |

Unchanged in substance since the FU was filed. The remedy is a prompt edit in 23 files, and
`_tools/task_edit.py` (the approval-free editor for SKILL.md, named in 5 prompts) is the
reachable door for it.

### 1.5 NOT BUILT — nothing to be reachable yet
- **R1's four-part bar.** Recursive search of `app\*.py` and `tools\*.py`:
  `ukey` → **0 files**, `fabricated` → **0**, `unassessed` → **0**, `provenance` → 2,
  `risk_tier` → 5. Same terms over `schema\*.sql`: `ukey` 0, `fabricated` 0, `unassessed` 0.
  Sanity check that the search works: `risk_tier` in `app\*.py` → 5, `def ` in `tools\*.py`
  → 105. So **two of the four bar components have no repo-side implementation at all**.
  `COULD_NOT_DETERMINE` for the prod Postgres corpus — the repo schema here is the app's
  DuckDB one. Measurement: `select count(*) from server_scores where ...` against prod.
- **R2's floor mover.** `oldest_scored_at` appears in **zero** files under `app\` or
  `tools\`. It lives in two ROOT modules — `freshness_gate.py` and
  `scoring_freshness_surface.py` — which are outside the ruff-blocking tree
  (`zo_sentinel tests/ci tests/gates app tools`, `pr-gates.yml` static-analysis job) and
  outside the pytest allowlist. See §2/R2.
- **R5 indexability.** `sitemap` → **0** files in `app\`, `tools\`, `tests\`; `robots.txt`
  → **0** in `app\`. The single axis a smaller rival is unambiguously ahead on has no code.

### 1.6 COVERAGE — what I did and did not check

**Covered (13):** FU-265, FU-268, FU-269 (as R1), FU-251, FU-306/318, FU-309, FU-343,
FU-359, FU-361 (as R2), FU-371, FU-376 (partial), FU-377, plus the `unblock.py` /
`tower_path_doors.py` adoption census.

**NOT covered — named so nobody reads this as a clean bill:** FU-262, FU-263, FU-298,
FU-310, FU-313, FU-314, FU-315, FU-338, FU-342, FU-344, FU-379, FU-382 (all §1 KEEPs whose
subjects are tower-side probes/ledger apparatus, not repo code), and the whole §2
infrastructure block (FU-024/027/028/065/107/109/115/117/134/149/190/207/228/232/235/236/
238/239/242/245/253/254 …) whose reachability is a question about prod and the tower, not
about this clone. Two worth pulling forward next:
- **FU-382** — `_tools` has no file whose name contains `floor` other than
  `_c35_floor_arm.py` and `_scratch_floor_20260808.py`; the ownership-key fix has no
  obvious durable home. Measurement: `grep -rln "ownership\|owner_key" _tools/*.py`.
- **FU-342 / the money guard** — see §2/R2: `tools/rescore/spend_guard.py` has **zero**
  tests anywhere in `tests/`.

---

## 2 — JOB 2: TEST SEAMS FOR R1–R6

**Standing rule applied:** run your verify BEFORE the change and require it RED. A roadmap
item whose failure condition cannot be made to fire today has no control by construction.

### 2.1 The CI pytest allowlist — exactly what it collects

The required context named `pytest` is **`.github/workflows/evaluator.yml`**, job `pytest`
(line 21). It is the only workflow that runs a per-file selection; `integration.yml` and
`goose-canary.yml` run `pytest tests/integration` (directory, not required),
`e2e-nightly.yml` runs three named integration files (nightly, not on PRs).

`evaluator.yml` lines ~100-147 collect these **48 files, and nothing else**:

```
tests/test_scoring.py                     tests/test_evaluator_smoke.py
tests/test_schema_loader.py               tests/test_duckdb_schema_uptime_probe.py
tests/test_proposed_to_pending_promoter.py tests/test_hollow_gate.py
tests/test_park_hollow.py                 tests/test_sft_ingest.py
tests/test_artifact_ingestor.py           tests/test_ingestor_governor.py
tests/test_breaker_autorecover.py         tests/test_breaker_retire.py
tests/test_ladder_routing.py              tests/test_pr_publisher.py
tests/test_build_routing.py               tests/test_queue_janitor.py
tests/test_engine_build.py                tests/test_anchor_refill.py
tests/test_policy.py                      tests/test_policy_chain_integration.py
tests/test_directive_simplifier.py        tests/test_perspectives_ask.py
tests/test_vast_jobs.py                   tests/test_vuln_and_scan.py
tests/test_vuln_enrich_otx.py             tests/test_glama_counts.py
tests/test_tier_backfill_trust_cap.py     tests/test_vuln_surfacing.py
tests/test_freshness_metadata.py          tests/test_cadence_admin.py
tests/test_dockerfile_copy_covers_active_services.py
tests/test_deploy_prod_script.py          tests/test_migration_graph_single_head.py
tests/test_fire_gate.py                   tests/test_sha_green.py
tests/test_ops_audit_state.py             tests/test_schema_truth_current.py
tests/test_queue_census.py                tests/test_graph_gap_directives_wired.py
tests/test_undeclared_write_guard.py      tests/test_rescore_billed_dph.py
tests/test_rescore_lstree_parse.py        tests/test_rescore_open_run_detection.py
tests/test_rescore_trust_priority.py      tests/test_rescore_refresh_yield.py
tests/test_vast_spend_selftest.py         tests/test_promoter_repair_wiring.py
tests/test_canary_classify_smoke.py
```

Measured facts:
- 48 allowlisted paths, **0 missing on disk**.
- `tests/` holds **146** `test_*.py` files → **98 files (67%) are collected by no required
  check on any PR.**
- **Collected count, local, `pytest --collect-only -q` over the 48: `661 tests collected`.**
  Environment: Windows, Python 3.11.9, pytest 9.0.3, cwd = the clean clone.

**The 661 is validated against CI, not just asserted.** FU-359 records the required check
reporting `635 passed` immediately after squash `af6c32e5`. The allowlist at `af6c32e5`
had 46 files; HEAD has 48; the two added since are `tests/test_canary_classify_smoke.py`
and `tests/test_promoter_repair_wiring.py`, which collect **26** together. 635 + 26 = **661**.
Local collection and CI collection agree exactly, so 661 is a usable prediction anchor for
the next green.

### 2.2 FU-359 — verified, and the answer is NO, it is no longer true

FU-359 (`FOLLOWUPS.md`, `status: resolved`, priority P1) claimed: *"The required `pytest`
check has never collected a single moat-rescore test."*

**That was true until 2026-09-01 and is false today.** Evidence:

1. **The allowlist names them.** `evaluator.yml` carries a dated comment — *"2026-09-01: the
   five tests/test_rescore_*.py files and the vast_spend self-test ADDED. Until today NONE
   of them was collected here"* — followed by the six paths in the `python -m pytest`
   invocation. Landed in PR #4365, squash `af6c32e5`
   (`git log -- .github/workflows/evaluator.yml` → `af6c32e5_2026-09-01_fix(rescore): read
   the refresh-half yield the harness has been discarding (#4365)`).
2. **They exercise the real GPU-spending harness, not a fixture.** All five load
   `tools/rescore/weekly_rescore.py` **by file path**:
   `tests/test_rescore_billed_dph.py:22`, `test_rescore_lstree_parse.py:27`,
   `test_rescore_open_run_detection.py:21`, `test_rescore_refresh_yield.py:43`,
   `test_rescore_trust_priority.py:34` — each followed by
   `importlib.util.spec_from_file_location("weekly_rescore", MODULE_PATH)`.
   `tests/test_vast_spend_selftest.py:23` does the same for `tools/rescore/vast_spend.py`.
3. **Independent count.** `python -m pytest --collect-only -q` over those six files →
   **`58 tests collected in 0.84s`** — the same 58 the ledger's own arithmetic (577 → 635)
   predicted before the push. The prediction and my independent observation agree.

**But R2 must not read that as "the GPU path is covered."** Contents of `tools/rescore/`
and their required-check coverage:

| module | referenced by any test | in the allowlist |
|---|---|---|
| `weekly_rescore.py` | 5 rescore tests + `test_fly_token.py` | **YES** (5 of 6) |
| `vast_spend.py` | `test_vast_spend_selftest.py` | **YES** |
| `wedge_guard.py` | `test_rescore_open_run_detection.py` | YES |
| `score_validity.py` | `test_rescore_trust_priority.py` | YES |
| `apply_risk_tier_backfill.py` | `test_tier_backfill_trust_cap.py` (yes), `test_tier_invariant.py` (**no**) | partial |
| `delta_report.py` | `test_fly_token.py` | **NO** |
| **`spend_guard.py`** | **nothing, anywhere in `tests/`** | **NO** |

`findstr /s /m /i /c:"spend_guard" tests\*.py` returns **empty**. The module named
`spend_guard` — the thing standing between a rescore wave and the $3/wave, $8/week, $25 MTD
ceilings that the roadmap treats as a hard halt — has **no test in the repository at all**,
collected or otherwise. This is FU-359's exact class, one module further down the same path,
and it is load-bearing for R2's cost failure condition.

### 2.3 Seam status per roadmap item

| item | can success/failure be measured TODAY? | what is missing | seam file | predicted collected-count change |
|---|---|---|---|---|
| **R1** — defensibly-assessed census | **NO** | Two of four bar components (`ukey=sid`, `fabricated=0`) exist nowhere in `app/`, `tools/` or `schema/*.sql`. There is no query to make RED. | new `tests/test_defensible_assessed_census.py` | 661 → **667** (6 tests: one per bar component, one asserting `assessed` ≠ `catalogued` ≠ `unassessed` as three distinct numbers, one asserting ±0.5% reproducibility across two builds of the same input). Must be added to the `evaluator.yml` allowlist in the SAME commit or it is collected by nothing. |
| **R2** — unfreeze the corpus floor | **PARTIALLY** | The harness is now covered (58 tests) but the floor predicate is not. `oldest_scored_at` lives in root `freshness_gate.py` / `scoring_freshness_surface.py`, which the blocking ruff tree excludes and the pytest allowlist excludes. `spend_guard.py` has zero tests, so R2's **cost** failure condition (">$3 to move the floor <1 day") cannot be made to fire. | (a) allowlist `tests/test_freshness_gate.py` + `tests/test_accept_gate.py`; (b) new `tests/test_spend_guard.py`; (c) new `tests/test_rescore_floor_advance.py` asserting cohort selection reaches rows older than the floor | (a) is free and green today: `pytest -q` over `test_freshness_gate.py test_accept_gate.py test_tier_invariant.py test_fly_token.py` → **`69 passed in 6.60s`**, rc=0. Adding the four: 661 → **730** (+10 +42 +5 +12). Adding (b)+(c) on top: **730 → ~742**. |
| **R3** — retire pre-adapter-fix scores | **NO** | Needs R2's floor moving first, and needs a date predicate against the prod corpus. No repo seam; `COULD_NOT_DETERMINE` on prod. Measurement that settles it: `select count(*) from server_scores where scored_at < '<adapter-fix commit date>'` against prod Postgres, run twice a week apart — a count that does not fall means R3 is not progressing. | new `tests/test_prefix_fix_excluded_from_assessed.py`, riding R1's census function | +3 (pre-fix row counted / not counted / date-unknown ⇒ fail-visible UNKNOWN) |
| **R4** — honest headline + tier calibration | **PARTIALLY** | `risk_tier` exists in 5 `app/` files, so the tier half is measurable. The headline half depends on R1's census, which does not exist. `unassessed` appears in 0 files, so "no card counts unassessed rows as scored" is currently unfalsifiable. `tests/test_tier_invariant.py` (5 tests) already exists and is **not** collected. | allowlist `tests/test_tier_invariant.py`; new `tests/test_dashboard_counts_split.py` | +5 from the existing file (included in R2's 730 above if done together), then +4 for the new one |
| **R5** — shopfront + indexability | **NO** | Zero `sitemap` / `robots.txt` code in `app/`, `tools/`, `tests/`. The success measure ("`site:` query returns >0 assessment pages") is an external observation with no in-repo control at all; the scorecard's failure measure ("computes signals the app tier does not hold", FU-080) has none either. | new `tests/test_sitemap_route.py` + `tests/test_scorecard_signal_availability.py` (the second must assert the signal is READ from the app tier, not defaulted — the FU-080 blocker) | +8 (4 route/format + 4 signal-availability, one per legible axis) |
| **R6** — fleet hygiene + the no-status sweep | **YES, today, with no new code** | Both halves are already measurable; nothing runs them on a schedule. | tower-side, not CI: wire the two commands below into `follow-up-triage` | no CI count change; see below |

**R6 is the one item that needs no seam built — it needs a caller.** Both measurements ran
clean just now:

- *No-status sweep.* Parsing `D:\zo\Zocomputer Agents\FOLLOWUPS.md` by `^#{2,3} FU-` heading
  and testing each entry body for the substring `status:` gives **375 entries, 37 with no
  status**: FU-262, 263, 268, 277, 289, 290, 298, 304, 305, 306, 309, 310, 311, 312, 313,
  314, 315, 318, 331, 334, 335, 338, 341, 342, 343, 344, 345, 351, 352, 370, 371, 376, 377,
  378, 379, 380, 382. Run 1 reported 43; the six that have since acquired a status include
  FU-359 itself. `_tools/fu_ledger.py` **already** has the fallback parse for standalone
  `- status:` lines (lines 216-231, with the "UNKNOWN is not zero (R6)" comment), so the
  capability exists; what is missing is a lane instructed to run the sweep. **Use `max()`
  over `^#{2,3} FU-` headings, never the tail heading — the ledger is not in numeric order.**
- *Orphan count.* `_ultraplan/registered_tasks.json` (snapshot 2026-09-02T19:20Z) lists
  **21 registered tasks**; `dir /s /b SKILL.md | find /c /v ""` over the live store gives
  **36 dirs**. **15 orphans**, matching FU-171's "16 orphans + 2 disabled". R6 success is
  `36 → 21`; R6 failure is any orphan still carrying a live-looking prompt at window end.

**A caution for R6 that the roadmap does not state.** Its own success criterion is "triage's
next run assigns a status to every currently-no-status entry". A lane that satisfies that by
writing a status without re-verifying converts 37 unknowns into 37 *asserted* values —
UNKNOWN laundered into a number. The correct completion is a status per entry **plus** the
cited evidence run, which is exactly what Run 1's dispositions file already says in its
governing rule on SUPERSEDED.

### 2.4 Prediction discipline

Any commit that adds a test file **and** touches `evaluator.yml` must state the expected
collected count in the commit message before the push. The anchors are:

| change | predicted `N passed` on the required `pytest` check |
|---|---|
| baseline, HEAD `0e92b5b3` | **661** |
| + `test_freshness_gate.py` (10) | 671 |
| + `test_accept_gate.py` (42) | 713 |
| + `test_tier_invariant.py` (5) | 718 |
| + `test_fly_token.py` (12) | 730 |

A green that reports a number equal to the previous day's is a check that did not see you.

---

## 3 — THE THREE THINGS A READER SHOULD ACT ON FIRST

1. **`tower_path_doors.py --check` is RED with 4 unguarded doors and has had zero readers
   for 20 days.** One of them (`fu_verify.py --negative-control`) is on the tool 14 of 36
   lanes use to close follow-ups. Cost to fix the comms: one line in one prompt.
2. **`tools/rescore/spend_guard.py` has no test anywhere.** R2's cost failure condition is
   unfalsifiable while that is true, and R2 is a P0 that spends money.
3. **77 of 1,133 staged service directories have no `service.toml`** and no PR-time gate
   says so. The gate exists and is correct — for `active/` only.
