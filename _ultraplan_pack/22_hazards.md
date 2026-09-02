# HAZARD CORPUS -- what this system has already learned the hard way

Built 2026-09-02T19:53:48+00:00.

READ THIS BEFORE PROPOSING ANYTHING. Every entry below is a move that
was tried and bit. The single highest-value thing a fresh plan can do
with this pack is avoid re-proposing a cure that is already recorded as
dead. Several standing hazards are specifically about PLANS:

  - a halt or proposal can outlive the condition that justified it, so
    ask whether the PREMISE is still true, not whether the argument is
    sound;
  - a cure wired into one door of eight reads as a cure;
  - existence is not adoption -- a tool that exists and is never called
    has not landed;
  - a remedy is only a cure if it is reachable from the surface that was
    bitten.

Source: `C:\Users\robin\AppData\Roaming\Claude\local-agent-mode-sessions\298dbca7-8b3f-430d-964f-267580894916\a08e9de6-02b6-45d0-bed3-a6168ee027f2\spaces\00f21be3-4eb9-44f8-8bc1-1c0ca76ff1ee\memory` (532 files). Full text of any entry is at that path -- open it when a hazard becomes load-bearing.

---

## MEMORY.md -- the standing index, verbatim

> INDEX ONLY — a hook per memory, not a summary. Detail lives in the linked file; open it
> before acting. A root index that stops loading takes every memory with it, so **never grow
> a bullet here, grow the target file**. Adding an entry? Trim another in the same edit.
> Recompacted 08-06, 08-09 (x2), 08-10, 08-11 (x2), 08-13, 08-23, **09-01 (gates section → hop-2; 24 entries moved, all 115 links kept, 20.8KB → under 17.1KB)**.
> **Two hops max: if a section needs a third, shorten hooks.**

## Standing authority — read BEFORE deciding to ask

- [**WEEK-2 RETRY OVER 08-29**](week_2_retry_charter_product_first_issues_on_github.md) — product-first + **poll GH issues every run** OUTLIVE the window; scorecard in briefing 08-31
- [Authority envelope](standing_authority_envelope.md) — ACT; FIRE_ON_GREEN; $3/wave · $8/wk · halt **$25** MTD
- [**BAR-CSV MACHINE WRITER ERASES GRADED ROWS**](the_bar_csv_machine_writer_erases_any_graded_row_it_shares_a_date_with.md) — proven 08-31; proposal pending adversary; check `--status --id bar-csv-...` + ARMING every run
- [**PEER REVIEW REPLACED THE CHAIRMAN GATE**](peer_review_replaced_the_chairman_gate.md) — 4 clauses peer-clearable; `data_deletion`+`above_the_ceilings` FOREVER_HELD. **PROPOSE, never halt**
- [**PERMISSION VALUE NO CODE COULD READ**](a_permission_value_no_code_path_could_read.md) — FU-265. Predicates as `subprocess`, never imports
- [**HALT OUTLIVED ITS CONDITION 23h; 09-02 a PROPOSAL outlived its premise 22h**](a_halt_outlived_the_condition_that_justified_it.md) — valid only against **live state**. As adversary ask "is the PREMISE still true?", not "is the argument sound?"
- [**"WIRE IT" WAS FORBIDDEN**](the_only_exit_from_the_census_was_keyed_on_a_name_that_is_not_unique.md) — ask what FORBIDS it. **`_tools\` is NOT a git repo**
- [**PROMPT IS OURS, SCHEDULE IS NOT**](the_approval_free_lever_is_the_prompt_not_the_schedule.md) — kill switch `_tools/lane_kill.py`
- [**"SKIP"→"MANUAL" IS DISPLAY ONLY**](the_skip_permissions_toggle_reverts_but_nothing_is_blocked.md) — run the two controls
- [**FALSE ZERO + PHANTOM STORE**](the_control_installed_to_catch_a_silent_stall_returned_a_silent_zero.md) — FU-278. **Run `_tools` scripts WINDOWS-SIDE**; fixing a read guard, grep the WRITE sites
- [**GUARD BEAT BY ITS OWN RESIDUE**](a_guard_was_defeated_by_the_residue_of_its_own_defect.md) — FU-289; **what does a guard RESOLVE AGAINST?**
- [**A PROBE THAT INLINES ITS SUBJECT**](a_falsification_probe_that_inlines_its_subject_freezes_its_verdict.md) — FU-290. **SUBPROCESS**; what does your verify return if the action SUCCEEDS?
- [**PROBE POLICE REFUSED EVERY PROBE**](a_guard_that_polices_probes_refused_every_well_formed_probe.md) — FU-296. **`--attempt-file` holds a COMMAND LINE, never source**
- [**REVERT_CHECK ON THE WRONG HOST**](a_revert_check_on_the_wrong_host_can_only_assert.md) — FU-301. **Which HOST sees the artifact?**
- [**8 OF 10 RAN NO CONTROL**](a_falsification_that_never_ran_a_control_is_unproven_not_wrong.md) — FU-300. **Uncontrolled ≠ wrong**
- [**READ A PROPOSAL: `--status --id`**](an_adversary_that_cannot_read_a_proposal_rubber_stamps_it.md) — no `--show`. **Positive control vs the KNOWN-BROKEN predecessor BEFORE filing**
- [**PROSE INSIDE A COMMAND FIELD**](a_state_nothing_writes_is_a_check_satisfied_vacuously_forever.md) — CLOSED 09-02. **Is your stored command a COMMAND?** Grep the WRITE SITE; never write the missing SHA
- [reboot: StartupTask not Run key](the_tower_survives_a_reboot_but_the_run_key_does_not.md) · [**ALLOWLIST ≠ THE BUTTON**](the_allowlist_was_not_the_thing_holding_the_button.md) — SKILL.md IS the store; edit via `_tools/task_edit.py`

## BLOCKED? — read this BEFORE shrinking a task or stalling

- [**ASK WHAT YOU ALREADY HOLD**](when_blocked_ask_what_you_already_hold.md) — `_tools/unblock.py --for <symptom>`. 4 keys live · vast $14.77 · **15 dark tools, 6 are ONE chain**

## Mechanical hazards — read BEFORE writing a shell call or trusting a timeout

- [**fu_ledger: TWO DIVERGED COPIES, sys.path PICKS THE WRITER**](the_tools_fu_ledger_copy_is_a_diverged_shadow_not_a_stale_one.md) — import by FILE PATH **with `sys.modules[name]=m` first, or dataclasses dies [identically on both copies](exec_module_without_sys_modules_mimics_the_diverged_copy_hazard.md)**; assert LF-count growth
- **WRITING FOLLOWUPS.md** — [per FILE, in BINARY, EVERY time](editing_a_crlf_ledger_in_text_mode_strips_every_cr.md) (flipped 7×; **08-13 100% LF** — assert the RATIO class, and **total growth vs the BACKUP**, never a convention-encoding +1) · [two bugs masked each other](two_terminator_defects_masked_each_other_in_the_ledger_writer.md) · [tail heading ≠ max](the_followups_ledger_is_not_in_numeric_order.md), use `max()` over `^#{2,3} FU-`
- [**RUNNING VERDICT WITH NO BASIS**](a_running_verdict_with_no_basis_cannot_be_told_from_a_hang.md) — FU-342. `-u` after the interpreter; a buffered `0 B` reads as DEAD. **`cd` ≠ .NET CWD**
- [**CONSTRUCTOR GAVE CHILD cp1252**](the_safe_constructor_handed_the_child_a_cp1252_stdout.md) — cure encoders at `main()` entry, BOTH streams (09-01: `--grep`, 1 door of 5). **A crash while REPORTING forges a clean "no match"** — assert on STDERR, never rc
- [**MCP CUTS THE SHELL, NOT THE CHILD**](a_hazard_written_in_a_task_prompt_cannot_reach_another_lane.md) — **~55–90s whatever the timeout arg**. Confirm by ARTIFACT
- [**RUN AS `friction.py --run FILE`; `--wait 5..25`**](the_anti_friction_guard_was_armed_on_the_path_nobody_takes.md) — 0/1/**2 NEVER launched**/3 · [**BOUND ≠ EMITTED VALUE**](a_clamp_whose_bound_is_the_only_value_it_emits.md) — ceiling 45→25
- [**PER-TOKEN → PER-CALL → PER-CHAIN**](a_bound_checked_per_token_cannot_see_what_the_transport_cuts_per_call.md) — FU-337. **NEVER chain two launches/polls in one call, whatever the sum says** (25+25=50 is legal and still lost); a cut line hides which segments never ran. Guarded 08-31; census call sites when you WIDEN a guard
- [**recurring_friction 0/30 EVER VERIFIED = 53% of the loop**](a_trailing_window_predicate_punishes_the_lane_that_reports_honestly.md) — vs dark_tool 40%. **SCOPED clears, fleet-wide trailing window never can**; check the KIND before reading a streak as underperformance. Rescope **FALSIFIED 09-01** — a forward window greens an uncured hazard; **do NOT execute it**, a v2 must key on evidence a cure LANDED
- [**TWO QUESTIONS, ONE CLOCK**](two_questions_sharing_one_clock_turn_a_race_into_a_terminal_verdict.md) — FU-330. **who owns each bound?**
- [**NO CURE REACHABLE FROM A PROMPT — 15 of 16**](a_remedy_is_only_a_cure_if_it_is_reachable_from_the_surface_that_was_bitten.md) — FU-306/333/**373**. Pipe payloads `@'…'@ | … --pysrc`. Ask **is it reachable from the SURFACE that was bitten**, not is it correct. **A refusal with no exit MANUFACTURES the hazard**. **09-02: an unwired cure ROTS (repair_staged_model_names dark 27d) AND a dark tool is UNTESTED — graph_domain_digest wrong twice, PR #4456. Run it, check its ARITHMETIC, before wiring it**
- [**PS ATE EVERY `$`**](powershell_double_quotes_ate_every_dollar_sign_in_a_ledger_write.md) — compose in `@'...'@`, then grep the written file
- [**SCRATCHPAD COPY = rc=0, 0 BYTES**](the_scratchpad_hazard_bit_three_lanes_and_the_census_never_saw_it.md) — `cp` via `/sessions/<s>/mnt/`, then STAT
- [**GUARD FLAGGED ITS OWN DOCS**](a_guard_keyed_on_whole_command_substrings_flags_its_own_documentation.md) — FU-341. Classify **PATHS**, never command substrings. **`is_file()==False` = absent OR invisible**
- [**DIFFED A SHARED DIR, KEPT NO KEY**](a_probe_that_diffs_a_shared_directory_has_no_ownership_key.md) — FU-382. What key says the artifact is YOURS? **A failing check that discards the child's output cannot be closed** — cycle-0048 sat 10 days
- **HOP 2 → [index_mechanical_hazards.md](index_mechanical_hazards.md)** — the 16 that bite less than daily, all live: probes that fake a pass (FU-267/268) · guards on the wrong branch (FU-201, `--sig`≡`--signature`, call-sites-not-families, 20 module copies) · detaching & cross-host (FU-062, `.started`, flyctl rc1)

## Counters & censuses that lied — read BEFORE publishing a count or calling something absent

- [**WORKTREE SAID 14, GIT CARRIED 4**](the_worktree_claimed_14_promotable_the_image_could_carry_4.md) — re-run promoter numbers on a **fresh clone**
- [**CLEARED, NOBODY EXECUTED**](a_cleared_decision_that_nobody_executes_is_a_decision_never_made.md) — scan CLEARED for `acted: null`; [it bit its own filer](a_cleared_decision_bit_the_lane_that_filed_it.md)
- [**ACTED + 2 GREENS + NO ARTIFACT**](a_verify_that_survives_success_cannot_witness_failure_to_act.md) — FU-313/314. **verify ≠ arming**; a capped bulk command picks by ORDER
- [**VERIFY ITS OWN REVERT FALSIFIES**](a_verify_whose_own_revert_falsifies_it_is_a_one_way_latch.md) — FU-338. **undated log line = undatable event**; rc=0 on a NO-OP
- [**RECEIPT ERASED, ERASER NEVER SAW IT**](a_receipt_confirmed_in_print_was_erased_by_a_writer_that_never_saw_it.md) — FU-351. **Reload shared docs at WRITE time**; flush stamps lastRunAt for unexecuted sessions
- [**THE KL DROPS FILES**](the_kl_drops_files_silently_and_nothing_ever_counted_the_drops.md) — 8/2390 FROZEN; every build is `graphify update` · rule out [FLATTENED IDS](the_kl_flattens_nested_ids_without_the_directory.md) first
- [**17 → 3 IN TWO MINUTES**](a_census_taken_during_a_live_fleet_rewrite_measures_the_rewrite.md) — re-count immediately before acting
- [**TREND SPLIT ON OBSERVED DAYS**](a_trend_split_on_observed_days_reads_a_dormancy_gap_as_a_rise.md) — FU-374. `loop_health` 26→68 RISING = 2 days vs 3 straddling a 6-day dormancy. **Split on CALENDAR days**; correcting it kept the red (37→57), which is how you know it wasn't a redefinition
- [**SCHEDULER DORMANCY x2; RESUME-DAY CENSUSES LIE**](the_scheduler_dormancy_is_a_recurring_class_and_resume_day_censuses_lie.md) — 3+ siblings silent ≈ one stopped scheduler; check lastRunAt stamps first
- [**R6 — UNKNOWN ≠ ZERO**](unknown_is_not_zero.md) — measured / genuinely-absent / could-not-determine. **Absence of an alert is not health**
- **HOP 2 → [index_counters_censuses.md](index_counters_censuses.md)** — the other 24 (all live): stale/dark inputs and spent premises · miskeyed and split counts · display-slice, type, and basis defects · two schedulers · ledger_lint span leaks

## Gates & predicates — read BEFORE trusting a green, a red, or a zero
- [**RED *AND* EMPTY — 87 PRs DAMMED**](a_red_check_that_runs_zero_tests_dams_the_whole_factory.md) — FU-256. **Merge on the COUNT, never the colour**
- [**SATISFIED *AND* EXPECTED AT ONCE**](a_required_context_can_be_satisfied_and_expected_at_the_same_time.md) — FU-366. `action_required` killed every PR run; **approve the WHOLE suite**; `statusCheckRollup` LIES, use `/commits/{sha}/check-runs`
- [**ONE IDENTICAL VERDICT = MEASURING THE WORLD**](the_reachability_ratchet_baseline_went_stale_and_became_a_level_gate.md) — FU-367. Stale baseline turns a DERIVATIVE gate into a LEVEL gate; 25/45 PRs red
- [**RUN VERIFY FIRST, REQUIRE IT RED**](run_your_verify_before_the_change_and_require_it_red.md) — FU-249. **No test seam = no control by construction**
- [**THE REPAIR THE ERROR INVITED WAS THE REGRESSION**](the_repair_an_error_message_invited_was_the_regression.md) — FU-363. The SHA the error asked for would have git-reverted a CORRECT patch into prod; **the jam was the safe state**. Ask what unjamming ARMS
- [**THE PROHIBITION WAS INSIDE THE FILE**](a_proposal_proposed_the_move_its_own_target_artifact_forbids_by_name.md) — FU-370. **Read the artifact's OWN text before proposing or clearing** · [awareness only where bitten](a_scanner_taught_that_raw_text_lies_applies_it_only_where_it_was_bitten.md)
- [**1 DOOR OF 8 READS AS A CURE**](a_cure_wired_into_one_door_of_eight_reads_as_a_cure.md) — FU-343. **Census every call site of the same shape IN THE SAME COMMIT**
- [**A FAMILY LABEL HIDES ITS CALL SITES**](a_hazard_family_label_hides_the_call_sites_it_is_made_of.md) — FU-377. 12 rows, 8 lanes, "cure already exists" → regroup by the **COMMAND cut**: 3 were the cure WORKING (rc=3 + poll handle), 2 were the orphan. `dark_tools.py` was 1 of 3 named slow tools with **no detach path at all**; 282.0s→2.5s. **Existence ≠ adoption; a graded rc must never detach**
- [**LOOP RANKED ITSELF AS SILENT**](a_lane_was_silent_because_nothing_ever_told_it_to_check_in.md) — FU-283; wire the obligation into a tool the lane cannot skip · [**LANDED ≠ WORKS**](a_component_is_landed_when_a_census_can_tell_used_from_unused.md) FU-371: score ledger reached **1 of 35 lanes, 0 of 5 obligation tools**. Census USED/UNUSED/**NEVER-TOLD**; fix the comms, **never add a gate**
- [**GREEN GRADES A DEAD TREE**](a_green_prs_checks_grade_a_tree_that_may_no_longer_exist.md) — `git log <merge-base>..origin/main -- <files>` before merging an aged PR
- [**"LEFT ALIVE" HAS NO SECOND HALF**](left_alive_for_forensics_has_no_owner_for_the_second_half.md) — FU-365. Guard keyed on `loading`; the bill is in `running`. **Read the launcher's own `fire.err`**
- **HOP 2 → [index_gates_predicates.md](index_gates_predicates.md)** — the 24 that bite less than daily, all live: predicate/probe defects (FU-195/249/260/285/348/362) · detectors blind to their own family · sweeps, fixtures and idempotence guards · `--verify` detach, lint-refused class, the money class

## Deploy, prod & infra — read BEFORE deciding a reload is unnecessary

- [**IMPORT CLOSURE + CONTENT DIFF, NOT ENTRYPOINT/MTIME**](a_daemon_is_stale_when_its_import_closure_changed_not_its_entrypoint.md) — an ff restamps mtime byte-identically
- [**HEARTBEAT FROM A PRE-MUTATION IMPORT**](a_healthy_heartbeat_from_a_process_that_imported_before_the_mutation.md) — FU-349. Import in a **SUBPROCESS**; **compare PIDs**; tracked-file edit dies at next `safe_ff`
- [**MEMORY MCP BLOCKED ITS OWN HANDSHAKE**](the_memory_mcp_blocked_its_own_handshake.md) — slow startup before initialize = invisible server; `_tools/rot_detector.py` audits both memory stores
- [**A LANE DROVE A DAEMON'S RESOURCE**](a_lane_drove_a_resource_a_daemon_already_drives.md) — FU-061. **`ps aux` BEFORE you retry**
- [**APP NAME ≠ HOST**](the_fly_app_name_is_not_the_canonical_host.md) — app `mcplookup`, host **mcprisky.io**; the 301 DOWNGRADES POST→GET
- [**v70 CLOSED 49 COMMITS**](a_hold_worded_on_merit_is_what_lets_the_next_run_fire.md) — `fire_gate --staged X --target X` is TAUTOLOGICAL
- [**FORCE ≠ FIXED**](a_single_force_loses_to_a_reconnecting_supervisor.md) — holder `flypgadmin` reconnects inside the window
- [preimport≠pre](a_file_named_preimport_was_written_after_the_import.md) rollback by CONTENT · [ZERO ROWS ≠ ERROR](zero_rows_is_not_an_error.md) `count(*)::text` · [NAMED TEST GONE](the_named_test_did_not_exist.md) FU-244

## Builder, scoring & product surface

- [**ASK/CVE: retrieval half LANDED PR#3913 08-24**](ask_cannot_see_cves_and_the_cve_feeds_were_never_promoted.md) — arm = deploy + admin reindex; population half (nvd/ghsa STAGED) still open
- [**WORST BUILDER FAMILY WAS SPELLING**](the_builders_worst_family_was_spelling_and_the_directive_taught_it.md) — log ROTATED 08-09 → **UNKNOWN, re-baseline forward**
- [**CURE ON THE 8/DAY PATH, NOT 588**](a_docstring_asserted_a_cure_the_code_did_not_contain.md) — FU-329. Ask each call site's **RATE**
- [**MEASURING TOOL REPAIRED 67 FILES**](a_tool_that_repairs_in_order_to_measure_throws_the_repair_away.md) — `git status --porcelain` before/after
- [**16 PRs BORN UNMERGEABLE**](sixteen_prs_were_born_unmergeable_by_landing_code_in_the_registry_directory.md) — FU-309. Gate is RIGHT. **Relocate to staged, NEVER close**
- [**SOA LANE 1082/1082 DEAD**](a_required_param_the_caller_never_passed_made_the_atomic_unit_unbuildable.md) — FU-246; fix = per-recipe ATTEMPTS denominator
- [**GREEN ON A DEAD WEBHOOK**](a_backfills_negative_control_is_consumed_by_its_own_first_success.md) — close on `clerk_synced_via='webhook'>=1`
- [**43.2h WAS NEVER A LAG**](a_lag_measured_across_a_dead_run_is_not_a_lag.md) — filter by `run` id · [MARGIN WAS n=1](a_margin_called_structural_was_derived_from_one_sample.md)
- [**MOAT: WAVE LANDED 08-31, SLA GREEN**](moat_rescore_baselines.md) — **09-01 basis: registry 498,694 / scored 296,109 / cov 59.4%** (old ~462k/~172k baselines are DEAD). `never_scored` is NOT a backlog; driver is `_wt_camp`. **staged 1,229 → active 464 → 32 mounted in prod = the real attrition**
- [**REFRESH HALF: 1 CHANGE IN 60,000**](the_refresh_half_produced_one_change_in_sixty_thousand_slots.md) — instrument shipped #4365; **don't reach for `--refresh-cap`**
- [**CORPUS FLOOR FROZEN SINCE 07-19**](the_refresh_half_of_the_cohort_cannot_move_the_corpus_floor.md) — FU-361. SLA reads a `max()`; oldest score is 69d and cannot age
- [**COUNT DIDN'T MOVE = RAN NONE OF YOUR TESTS**](a_green_whose_count_did_not_move_ran_none_of_your_tests.md) — CI pytest is a per-file allowlist; **predict the count before you push**
- [**CARRIED ≠ MEASURED**](a_carried_forward_value_is_indistinguishable_from_a_measured_one.md) — `/freshness` never emits `trusted_servers`; write EMPTY
- [**NEVER-NONZERO READ AS NOMINAL**](a_counter_read_as_nominal_had_never_once_been_seen_non_zero.md) — cadence `events_queued` 0×4 → **22125** (96) → **caused 0** (98). **PAIR CLOSED 09-02: a control needs BOTH halves** — non-zero proves the path fires, a *caused* zero proves the zero means what you say. Cite 96+98, never 96 alone

## HOP 2 — [index_domain_catalogs.md](index_domain_catalogs.md)

Open when an instrument disagrees with reality. Holds: **gates/monitors that lied** (10 resolved) · **Zo email one-way · 08-03/04 prod fires · staged→active cohort · prod migrations** · the **~60 remaining entries of the 51% class** · **domain catalogs** (moat/scoring · deploy/infra · product surface · loop/builder).


---

## Every memory file: name, description, opening

### a_backfills_negative_control_is_consumed_by_its_own_first_success.md
*"clerk_reconcile's webhook-miss control is one-shot per user — the backfill that detects the outage also erases the evidence, so tomorrow is GREEN on a dead webhook"*
`tools/clerk_reconcile.py` proves the Clerk webhook is alive by a clever control: a user THIS JOB created whose signup is older than `CLERK_WEBHOOK_STALE_HOURS` is proof the live path did not deliver it. On 2026-08-04, its **first ever execution**, it returned rc=1 RED — `clerk_users_seen: 3, created: 3`, misses aged 383.52h / 851.83h / 898.87h. The webhook had delivered **0 of 3 signups ever**, n…

### a_bare_powershell_range_argument_returns_zero_commits.md
*"FU-243 — a bare `$sha..ref` git argument in PowerShell returns 0 commits with rc=0, and 0 is the value that ends a prod-drift run early"*
Measured 2026-08-04T04:49Z in `D:\zo\_lanes\prod-drift`, same repo, same second, six forms of one revision range:  | form | result | |---|---| | bare literal `<sha>..origin/main` | 20 | | **bare variable `$p..origin/main`** | **0** ← wrong | | double-quoted `"$p..origin/main"` | 20 | | concatenated `($p + '..origin/main')` | 20 | | braced `"${p}..origin/main"` | 20 | | Python `subprocess` argv | 2…

### a_blocked_backlog_is_not_a_blocked_commit.md
*"The prod hazard class is decided per-COMMIT by `git rev-parse <sha>:migrations`, so a Class-B backlog can contain a long Class-A prefix that four runs held for nothing — walk the cut point before concluding the drift is blocked."*
On 2026-08-03 prod-drift-sentinel had held prod at v66 `d5cb1d0f` for four consecutive runs, 109 commits behind main, on [[the_blocker_was_an_unfinished_wiring_not_a_missing_privilege]] (FU-235). Every hold was correct. Every hold was also **over-broad**, and nothing in the lane could see it, because every run asked *"is the drift fireable?"* and the drift was being treated as one object.  **Class…

### a_blocked_fix_had_an_unclaimed_half_that_needed_no_permission.md
*"A hazard id sitting in friction.HAZARDS at ZERO rows was blocked for 3 days behind two clause-bearing proposals; only the RETRO-keying half ever needed permission, and the forward half was free the whole time"*
2026-08-07, vast-jobs-daily-audit. `scratchpad-invisible-to-tower` had existed in `friction.HAZARDS` for days at **0 rows** while the hazard bit 4 lanes. Two peer proposals tried to fix it (`scratchpad-silent-nothing-family`, `scratchpad-key-the-existing-family`) and **both were FALSIFIED**. I hit the hazard live, and got the family its first keyed row by passing the canonical id as the `klass` ar…

### a_body_captured_through_the_shell_loses_its_newlines.md
*"Round-tripping a SKILL/prompt body through Windows-MCP PowerShell collapsed all 222 newlines at rc=0; the control that catches it is an EQUALITY against the declared char count, never a tolerance"*
2026-08-11, `deploy-runtime-from-main`. Editing a task prompt means read-modify-write, and BOTH halves of the read have a silent defect.  1. **The transport eats newlines.** `python _tools\task_edit.py --show <task> --body`    piped through Windows-MCP PowerShell into `Out-File` returned 26805 B of prompt as    **ONE line** — all 222 newlines gone, rc=0 the whole way. Writing that back with    `--…

### a_bound_checked_per_token_cannot_see_what_the_transport_cuts_per_call.md
*"FU-337 — per-TOKEN, then per-CALL, now per-CHAIN: the bound is right and the shape is what bites. Two chained polls at a legal 25s each still lose the second one silently. Fixed 2026-08-31."*
**Ask what UNIT the thing you are guarding against operates on, then check the bound against THAT unit.** The MCP transport cuts THE CALL. `friction._ps_sleep_over_cap` compared **each** `Start-Sleep` token to 60 and never summed them, so `Start-Sleep -Seconds 40; work; Start-Sleep -Seconds 40` — 80s in one call — returned `no known hazard`. The bound was correct; the population it was applied to …

### a_carried_forward_value_is_indistinguishable_from_a_measured_one.md
*"plan_200k_count_log.csv restated trusted_servers=279,611 for 5 rows from a source the tower cannot reach; a flat series looked measured"*
`D:\zo\Zocomputer Agents\plan_200k_count_log.csv` column 4 `trusted_servers` shows **279,611** identically on 2026-07-30, 07-31, 08-01, 08-02 and 08-03. None of the last four could have measured it:  - `GET https://mcprisky.io/freshness` does **not** emit `trusted_servers`. The whole   payload is `scores_rows, scored_servers, registry_rows, never_scored, newest_scored_at,   oldest_scored_at, compu…

### a_case_sensitive_probe_reports_absent_for_a_guard_that_shouts.md
*"A peer proposal was filed to ship a guard that was already merged and armed, because its evidence command used case-sensitive findstr against code written in capitals."*
`reconcile-refuses-delegated-self-fire-v2` (prod-drift-sentinel, 2026-08-12T01:07:55Z) was **FALSIFIED** by deploy-runtime-from-main: its premise — *"three days after being ACTED, the guard does not exist in any artifact that runs"* — was already false when filed.  The refusal was live on `origin/main:tools/shadow_decision.py` lines 466–477 (`if human_fired and fired_by != "human": if _delegated_s…

### a_catch_all_bucket_publishes_the_diagnosis_it_was_named_for.md
*"The goose-canary namespacing probe cited FU-108 in its comments, implemented two buckets with the second a catch-all, and published 'genuine rename, DO NOT FLIP' on a version where the name resolved six times -- a dispatcher error that NAMES a tool is proof the tool resolved"*
2026-08-10 goose-shadow-research, canary run `31384666172`. The MANDATORY scar-#454 namespacing probe — the gate the live goose upgrade escalation rests on — printed:  > `CANARY FAIL (b): the architect's hardcoded tool name did NOT resolve ... this is a genuine rename. The 1.38 starvation shape. DO NOT FLIP.`  The name resolved **six times**. The log carried `-32602: Tool arguments for zo_directiv…

### a_census_in_the_working_tree_cannot_tell_shipped_from_scratch.md
*"staged=137 was a working-tree number; committed on origin/main is 100 — and the basis error produced a FALSE falsification of PR #2060"*
zo-sentinel, 2026-07-29. `promote_staged_to_active.py` runs in the runtime working tree, so it grades **committed services and uncommitted builder scratch identically**. The autopoiesis tracker published `staged 21 -> 56 -> 98 -> 137` for four days without ever stating that basis. Measured against a fresh clone at `origin/main`: **committed staged = 100**, working tree = 137. 38 directories are un…

### a_census_taken_during_a_live_fleet_rewrite_measures_the_rewrite.md
*"Counted 17 lanes with a stale away-window date, then 3 two minutes later — a sibling was repairing them live; and the one \"remaining\" hit was a legitimate verify predicate, not a window date"*
2026-08-06, confirming vacation mode. Grepped the scheduled-task SKILL store for the superseded away-window start `2026-08-07` and got **17 of 35 lanes**. Two minutes later the same scan returned **3**, of which 2 already carried the correction note. A sibling session was rewriting the fleet's SKILLs *while I was counting them*.  **Why:** a census over a surface another lane is actively repairing …

### a_clamp_whose_bound_is_the_only_value_it_emits.md
*"2026-08-12 cycle-0041 — curing the long child moved mcp-timeout-orphan to the POLL: every detach path printed --wait 45, which was also the unset default AND MAX_CLI_WAIT_S itself, so the clamp was a no-op on the only value the fleet was ever handed. Ceiling now 25"*
**THE CEILING IS NOW 25, NOT 45. `--wait 5..25`. Anything that prints a bigger number is stale.**  `mcp-timeout-orphan` had been SELECTED SIX TIMES and the remedy was never what was missing. `improve_loop --select` (0028), `--verify` (0035) and `lane_start --lane` (0038) all self-detach, and `friction.self_detach()` is the one shared constructor. The family still took **38 rows in 7d across 9 lane…

### a_cleared_decision_bit_the_lane_that_filed_it.md
*"A peer decision I filed was CLEARED and left unexecuted for 14h, then the exact predicted failure happened to me — fabricating one C4 agreement out of a required eight."*
2026-08-09, prod-drift lane. I filed `reconcile-must-refuse-delegated-self-fire` on 08-07 after reconciling my own delegated fire with `--fired-sha`. It was CLEARED 08-08T11:08:10Z with a discriminating positive control, `acted: null`. 14h later I fired v74 and did **the identical thing again** — `consecutive_agreements` 4 → 5, a fabricated agreement out of a required run of 8.  **Why:** clearing …

### a_cleared_decision_that_nobody_executes_is_a_decision_never_made.md
*2026-08-06 — peer review CLEARS proposals but nothing assigns the act; check --status for CLEARED-and-unexecuted before hunting for new work*
**2026-08-06.** `peer_review --status` showed 5 decisions, **5 adjudicated, 0 pending past 24h,** median time-to-adjudication 1.4 h. Every instrument read green. But `cohort-must-be-committed-before-enforce` was **CLEARED** — and its action ("commit the honest cohort's 31 untracked files before any `--enforce`") **had never been performed**, ~24h on, with T2 dated 2 days out.  **Why:** the mechani…

### a_closed_family_reopened_beneath_every_top_n_list.md
*Family A went 0→6 hidden under an x39 leader — only an explicit lookup of the RENAMED TARGET can see a rename regression*
FU-279, measured 2026-08-07T14:39Z on `artifacts/staged_promotion_report.json` (generated 14:38:43Z, candidates 506, promote 14, hold 492), cross-checked against `app/models.py` in the runtime tree at 92caecd6.  **Family A** = casing / plural-prefix import drift onto a model that **REALLY EXISTS**. Closed as `e031cf6f` / PR #2701 (133 sites), read 0 for weeks. On 2026-08-07 it reads **6**: `Vulner…

### a_commands_runtime_can_be_supplied_by_its_argument.md
*"improve_loop --verify was scoped as \"fast\" by measuring its own code, but it executes the CANDIDATE'S predicate; and friction's --wait ceiling lived in a help string beside a parser that accepted 300"*
cycle-0035 (2026-08-10) closed two holes in `mcp-timeout-orphan`, the fleet's #1 recurring hazard (31 rows / 8 lanes in the trailing 7d). Both were **scoping** defects, not missing cures — the cures already existed and were pointed at the wrong object.  **1. A command's runtime can be supplied by its ARGUMENT, so measuring the command is measuring the wrong object.** cycle-0028 gave `improve_loop.…

### a_complement_is_not_a_backlog.md
*"2026-07-29 — /freshness never_scored read 188,189 and I projected coverage collapse from it. The TRUE backlog was 1,695. A complement (registry_rows - scored_servers) is not a work queue: 186,494 were URL-duplicates and 138,643 rows never enter the scorer at all. Distinct-URL coverage was 99.25%, not 59.73%."*
I logged `never_scored = 188,189` from `/freshness`, computed coverage at **59.73%**, projected it falling below 55% by 2026-09-10, and filed that as the finding of the day. Then the chairman authorised a wave against it and sizing the wave showed the number could not carry any of that weight.  `/freshness` computes `never_scored` as `registry_rows - scored_servers`. That is an **arithmetic comple…

### a_component_can_misattribute_its_own_starvation.md
*"The builder idled 471 cycles because the architect's SALVAGE path spells the service name `task` and the promoter only accepted `service_name|name` — while the architect's log blamed an exhausted anchor and asked for a human"*
2026-07-30. `goose_runner` logged `Total directives loaded: 0` for **104 consecutive cycles** (10:23:20Z → 12:09:14Z, ~106 min) while **212 valid `build_service` directives** sat in `directives/proposed/` renamed `.rejected`.  > The briefing and PR #2412's title originally said *"471 cycles"* and *"207/195"*. 471 was > `goose_runner`'s **monotonic cycle counter**, not a count — see > [[a_cycle_cou…

### a_component_is_landed_when_a_census_can_tell_used_from_unused.md
*"The autopoiesis score shipped good code into 1 of 35 lanes, 0 of 5 obligation tools, and a name collision - measure a new component's LANDING, not just whether it works"*
FU-371, 2026-09-01, chairman-directed. The AUTOPOIESIS score ledger (`_tools/autop_score.py` → `AUTOPOIESIS.md` + `autop_scores.jsonl`) was built in one hour and the code is good — 23/23 of its own controls pass, the writer refuses an unevidenced score, graded and MEASURED-ONLY rows are kept as separate populations. **The landing was the partial half:**      lanes in the live task store ..........…

### a_config_file_built_by_the_code_builder_is_code.md
*"43 staged service.toml files were PYTHON — the builder emitted a config file through the LLM generate_file path, so it got the code-module conventions (True, dict literals, __main__ self-test trailer)"*
zo-sentinel, 2026-07-29. FU-120 asked for two years of the wrong question: "why does a `write_raw` directive with inlined deterministic content emit different bytes than `service_decomposer.py:_service_toml()` generated?" It never does. The broken manifests **never went through `write_raw`** — they went through the LLM `generate_file` path, and therefore inherited every convention the builder appl…

### a_contract_that_cannot_fail_passed_and_no_hollow_agreed.md
*"The liveness contract that gates staged→active promotion passed a 75-byte comment-only file; the required `no-hollow` CI check passed it and a 32-byte stub too."*
Measured 2026-08-03T14:38:01Z on runtime `26e46c31`, promoter OBSERVE.  `services/staged/registry_source_freshness_dashboard/contract.py` is **75 bytes**, entire content `# First, let me check what the _exemplar looks like and the model structure` — the generating model's own deliberation written to disk as the deliverable. AST: **0 statements, no assert, no `__main__`**. `python -m ...contract` e…

### a_control_went_green_on_two_identical_error_messages.md
*"cycle-0028 — an equivalence probe compared two forms that had BOTH been rejected by argparse; \"fast\", \"rc agrees\" and \"payload non-empty\" all passed on two identical usage errors."*
Writing the negative control for FU-294 I compared `--status --detach` against `--status --no-detach` and asserted: returns fast, exit codes agree, payload non-empty, payloads match. On the pre-fix run **neither flag existed yet**, so argparse rejected both in 0.1s with rc=2 and a one-line usage error — and three of the four assertions went **GREEN**. Two identical error messages are fast, agree p…

### a_correct_document_can_answer_a_dead_question.md
*"prod-drift wrote a fully-evidenced STAGED_GREEN at 19:59Z for a fire that had already been attempted and aborted at 18:16Z — every number correct, the document false, because the ledger scan is enforced by nothing"*
2026-08-02, 19:47Z run. The lane measured drift (51 commits), main GREEN 7/7, 8/8 gates `gates_skipped=0`, COPY 12/12, backup 12.69h restore-verified, rollback anchor probed PULLABLE with a discriminating control, and dispatched a real image build. Then wrote `prod_deploy_staged.md` headed *"merit preconditions ALL MET"* with a ready one-click sequence.  **It read `FOLLOWUPS.md` afterwards** and f…

### a_correctly_raised_alarm_with_no_subscriber.md
*"cadence-jobs-daily-trigger missed 2026-07-31 entirely; /health flagged alert=true correctly for ~12h and nothing read it — the detector existed, the subscriber did not"*
2026-08-01. `cadence-jobs-daily-trigger` did not run on 2026-07-31 **at all** — FU-207's class ("a scheduled task's `lastRunAt` advanced for slots that did no work") in a second lane, so that defect is not specific to `prod-drift-sentinel`.  **Proving a run never started, cheaply.** `tools_cadence_fire.ps1` writes its `FIRE_START` line BEFORE it POSTs anything, so the log is a PRE-LAUNCH side effe…

### a_corroboration_both_hypotheses_predict_is_not_corroboration.md
*FU-211 — FU-210 checked the timezone and got it wrong; the one field it checked was the single field both readings predict identically*
FU-210 (#2522) rebuilt the run-ledger slot grid and hand-wrote it as UTC hours. The scheduler evaluates cron in **LOCAL** time (America/New_York). Three of four daily slots landed where no run can occur, and MISSED is an email condition — the fix would have gone from stale to **permanently red** in one day.  It did not assume the timezone. It checked, and wrote "measured, not assumed": `nextRunAt …

### a_could_not_determine_default_was_read_as_a_positive_attribution.md
*"verify_candidate.ps1 stamps produced_by_lane=unattributed from the shared checkout, and sentinel_run_ledger read that non-empty string as FOREIGN, excluding the lane's own verdict from the orphan test under a CLEAN verdict"*
FU-227, third instance. 2026-08-07T19:49:42Z, prod-drift-sentinel.  `ops/host/verify_candidate.ps1` resolves the producing lane from `$env:ZO_LANE`, else a `\_lanes\<name>` component of `$PSScriptRoot`, else the literal **`"unattributed"`**. Launched from the SHARED checkout `D:\zo\zo-sentinel\zo-sentinel\ops\host\` — the path the SKILL's own step 4 names — neither source resolves.  `sentinel_run_…

### a_count_field_can_hold_an_error_string.md
*"FU-312 — a source_count came back as \"error: TimeoutExpired\", 16 clean nights preceded it, and the consumer compared two equal error STRINGS and returned one as the row count."*
2026-08-10, score-import-shepherd. The nightly backup manifest writes `source_counts` by shelling `fly ssh console -C psql` with a 300s timeout. That call timed out on `orgs` and the manifest stored the literal string `error: TimeoutExpired: ... timed out after 300 seconds` **in a field typed by every reader as an integer** — while reporting `degraded:false`, `critical_failed:false` and `alerts[]`…

### a_counter_read_as_nominal_had_never_once_been_seen_non_zero.md
*"cadence events_queued was 0 in every recorded run and read as NOMINAL; 2026-09-01 run 96 returned 22125, retroactively proving the zeros were measured rather than a dead path"*
`perspective_snapshots` reported `events_queued: 0` in runs 85, 87, 89 and 91 — every value ever recorded in `cadence_pending_runs.json`. Two long SKILL sections (2026-08-06, 2026-08-12) taught how to read that zero as NOMINAL by resolving the `moat-rescore-weekly` cron from the scheduler and reading the rescore lane's VERDICT. **Both assumed the counter could move; nothing had ever demonstrated i…

### a_cumulative_total_is_not_a_series_if_the_log_can_be_truncated.md
*"The casing-repair metric was trended for 5 days as a cumulative grep of goose_runner.log; the log was truncated in place, so the \"series\" measured the file's coverage window, not the behaviour"*
`autopoiesis_bar.csv` tracked `casing_repairs_24h` as a cumulative `grep -c "casing-repair" /home/workspace/logs/goose_runner.log` and trended it 58 → 124 → 192 → **321 (7/29)** → **218 (7/30 12:23Z)** → **305 (7/30 14:52Z)**.  **A cumulative counter cannot fall.** It fell by 103 inside a single day, so the FILE changed, not the behaviour. Confirmed 2026-07-30:  - `head -1` of the live log is `[20…

### a_cure_wired_into_one_door_of_eight_reads_as_a_cure.md
*"FU-343 — cycle-0043 wired tower_invisible() into 1 of 8 doors that read a lane path; FU-342's -u cure reached 2 of 3 launchers. Census every call site of the same shape IN THE SAME COMMIT."*
**When you wire a classifier, guard or cure into the call site that bit you, run the CENSUS for every other call site of the same shape in the same commit** — and prefer the census to your memory of where the tool is used. One door fixed out of eight reads, to every counter and to the lane, as a cure.  Measured twice in one cycle (improvement-loop cycle-0045, 2026-08-13, FU-343):  1. cycle-0043 bu…

### a_cycle_counter_is_not_a_count_of_cycles.md
*"Published \"471 consecutive idle cycles\" from goose_runner's monotonic counter — the real figure was 104, a 4.5x overstatement. The adversarial pass found 6 errors in one briefing, 3 of them the doctrine's own rules."*
2026-07-30, `daily-chairman-review`. I headlined a decision record with **"471 consecutive idle cycles"**. `471` is `goose_runner`'s **monotonic cycle counter** as printed in `=== Cycle 471 ===` at 12:03Z. The actual idle run was cycles ~372 → 476 — **104 consecutive `Total directives loaded: 0` lines, 10:23:20Z → 12:09:14Z (~106 min)**. I overstated the outage **4.5×**, in the headline, in the le…

### a_daemon_is_stale_when_its_import_closure_changed_not_its_entrypoint.md
*"2026-08-04 — the FU-236 no-hollow fix landed on disk and ran UNLOADED for 70 min because goose_runner.py itself was unchanged; the prescribed \"did the entrypoint change?\" test says NO and skips the reload"*
On 2026-08-04 the daily ff took the runtime `26e46c31` → `80fa29cb` (52 commits), files landing on disk at **09:10:26Z**. In that range was `401cc09f` (PR #2761), the FU-236 fix that taught `no-hollow` to see a service unit — `zo_sentinel/gates/hollow.py`, +104/−1.  **`goose_runner.py` itself was NOT in the diff** (last touched `77fd0b1b`, 2026-07-28). So the reload test the deploy SKILL prescribe…

### a_daemon_roster_can_straddle_a_deploy.md
*"Did source change after the daemon started?" is N comparisons, not one — a rolling boot put 7 daemons before and 15 after a single ff.*
On 2026-08-02 the ZoComputer container cold-booted *during* the deploy run. The daemon roster started **09:09:30Z … 09:10:47Z**; `safe_ff.sh` completed at **09:10:06Z**. So 7 daemons (zo_sentinel_builder, ladder_shim, goose_runner, sentinel_directive_generator_goose, gate_scheduler, liveness_probe, signal_bridge) began on PRE-ff code and 15 (candidate_promoter_daemon onward) on POST-ff code, from …

### a_dark_consumers_default_input_is_the_stale_artifact.md
*"FU-311 — wiring the capmap chain's dark CONSUMER the obvious way (no flags) would have fed the builder 3 fixes for drift a BLOCKING gate scores as zero, because its default --graph is the committed, stale artifact"*
FU-311, improvement-loop cycle-0034, 2026-08-10. PR #3172 (70008eaa). Predicate `dark_tools.py --assert-wired tools/graph_gap_directives.py` **rc 1 → 0**; dark census **11 → 10**.  `tools/graph_gap_directives.py` ranked #1 dark (6517B). It is the CONSUMER end of the chain [[a_top_n_census_ranks_members_and_can_never_name_a_chain]] named; #3106 wired the two PRODUCERS and left the reader dark.  **T…

### a_decision_recorded_after_the_outcome_is_not_a_prediction.md
*"C4's shadow ledger counted an agreement written 4m55s AFTER the chairman fired; it policed WHO and HOW, never WHEN — PR #2296, FU-179"*
The C4 shadow-decision ledger (`tools/shadow_decision.py`, repo, since PR #2296) exists so Phase 2 autonomy is granted on evidence about **prod-drift-sentinel's own decision**, not on attended fires that never exercise it ([[prod_drift_sentinel_cofc]]). Its anti-gaming rules were careful: `--record` refuses `--acted yes`; the hazard class comes from the `migrations` tree object, never self-descrip…

### a_decision_surface_must_carry_current_state_only.md
*"prod_deploy_staged.md contradicted itself — a §5 header said STAGE-BLOCKED under a doc header saying READY, because verdicts were corrected by pre-pending, never by replacing"*
`prod_deploy_staged.md` — the document a human reads immediately before firing prod — grew to 21,219 bytes over 14 restages by **correcting a verdict by pre-pending a new one and leaving the old text beneath it**. On 2026-08-01 §5 was headed `Backup — ~~FRESH~~ **STALE, 36.04h — THIS IS THE BLOCKER**` (a 19:12Z measurement), followed by the superseded FRESH block, followed by a deadline 18h past —…

### a_detached_child_survives_the_transport_cut_but_not_the_host.md
*"FU-353. The zo box (modal) can reboot mid-run, killing setsid-detached children and wiping /tmp; and a sibling safe_ff can stash-evict a tracked artifact your run just wrote."*
2026-08-31, autopoiesis-bar-tracker. Three failure domains that look alike and are not:  1. **Reboot ≠ MCP cut.** The zo box rebooted at 03:54Z mid-run ("up 1 min" was the tell). A setsid-detached promoter child died silently and /tmp was wiped. `setsid` + `.done`-file polling survives the ~55–90s transport cut, NOT a host reboot. Put the log and `.done` on the persistent volume (`/home/workspace/…

### a_detector_cannot_tell_a_citation_from_an_obedience.md
*"rule_echo and loop_health both flagged text that RECORDED a dead rule / a debunked absolute as though it asserted it — the fix is a supersession marker near the quote, plus a third self-test point that is the exemption's own negative control"*
2026-08-05, prod-drift-sentinel (no-drift run). **A record of a dead rule is indistinguishable, to a substring match, from obeying it — and the same is true of a retracted claim.** Both detectors built in the 08-04 session tripped on this in the same run:  - `rule_echo --check` reported `lane:improvement-loop` STILL LIVE on *"only true HARD GUARDRAILS require a human"*. The surface was that lane's…

### a_detector_ships_pre_saturated_by_its_own_incident.md
*"backup_select.py cadence shipped 2026-08-01 to catch missed-nightly holes, and read rc=1 GAP on its very first healthy night — its only breach was the 36.24h hole that motivated it, which is immutable and sat in its own 14-day window until 2026-08-13."*
`cadence` was built on 2026-08-01 under FU-208 to find a HOLE in the backup series (a run that never happened leaves no artifact, so it is only visible as a gap). It worked. It was also **saturated from the first night after it shipped**: on 2026-08-02 the backup was a clean PASS — RC=0, restore_verify ok, restored counts equal source — and `cadence` still returned **rc=1 GAP**, because its only b…

### a_detectors_first_proof_must_be_the_motivating_incident.md
*A month-scoped gap detector passed every fixture and reported CLEAN on the live file — the missed day fell in a MONTH SEAM. Fixtures are written by the same misunderstanding that wrote the code.*
2026-08-01. Built `coverage()` in `tools/ops_audit_state.py` to surface days the daily ops audit did not run (the state file's `entries[]` has one writer, so a missing date is a missed run). First implementation scoped the scan to a month. It passed six tests and, run against the LIVE file, reported **clean**: July (07-26..07-30) complete, August (08-01) complete — because the missed day, **2026-0…

### a_docstring_asserted_a_cure_the_code_did_not_contain.md
*"FU-329 — the schema cure was wired to the 8/day canary, not the 588/day engine, and two docstrings claimed otherwise so nobody looked for weeks"*
2026-08-11. `goose_runner._soa_schema_excerpt()` inlines REAL column lists from the live `app.models` KL. It was reachable **only** from `_soa_service_spec` — the SOA goose canary, capped at **8 builds/day**. The engine path wrote **588** files that day and never saw it. schema-PRM blocked 775 builds on 08-10 and 454 by 12:05 on 08-11 (`McpServerRegistry.id` ×504 — the PK is `server_id`).  **Ask w…

### a_duplicated_safeguard_drifts_toward_the_observer.md
*"FU-157 - a copied helper does not drift randomly; the fix lands in the copy that WATCHES, never the one that ACTS"*
FU-157 (2026-07-28, PR #2185 merged `cb39f401`). `Reset-DisposableWorktree` — the retry-and-VERIFY worktree teardown written after the 3,466-file orphan scar — lived as two copies: `ops/host/verify_candidate.ps1` (the 3h gate) and `ops/host/deploy_prod.ps1` (the chairman's fire path). The observer had learned that an EMPTY leftover directory is harmless; the actor had not, and calls the helper `-M…

### a_fallback_surface_can_share_the_primarys_failure_mode.md
*"2026-07-31: the dated CSV row was adopted BECAUSE `lastRunAt` lies about whether a lane ran — and one ~12h quota outage took out both surfaces at once, the record lying 'ran' while the product's absence read as no-data rather than as an alarm"*
`plan-200k-count-tracker` was twice falsely called "skipped" off the scheduler view, so the 2026-07-19 ruling moved `daily-chairman-review`'s check onto **the dated row in `plan_200k_count_log.csv`** — a real product artifact instead of the scheduler's own record. Good fix. On 2026-07-31 a single ~12h weekly-usage-limit outage (~07:10Z → the 19:00Z reset) defeated **both surfaces in one event**: `…

### a_falsification_probe_that_inlines_its_subject_freezes_its_verdict.md
*"FU-290 — a peer-review falsification probe that copied its predicate \"verbatim\" kept printing BROKEN a day after the predicate was repaired, blocking a correct action with a photograph"*
2026-08-07 the adversary correctly FALSIFIED `enforce-first-cohort-max-per-run-1`: its `verify_cmd` (`cohort_trackedness.py`) read `git ls-files services/staged` only, and the action is `os.rename(services/staged/<svc> → services/active/<svc>)` — so the verify went **GREEN → RED because the action worked**, and `--sweep` would auto-revert every correct promotion.  FU-281 repaired the predicate to …

### a_falsification_that_never_ran_a_control_is_unproven_not_wrong.md
*"FU-300 — discrimination_proven is True by construction on every peer decision, so audit()'s UNPROVEN ATTEMPT note has never once been able to fire"*
`_tools/peer_review.py` computes `"discrimination_proven": bool(broke or pc_rc == 0)`. On a FALSIFIED row `broke` is True, so the flag is True **by construction**; on a CLEARED row a passing control is required, so it is True there too. `audit()`'s only discrimination reading — `if any(not r["falsification"].get("discrimination_proven"))` → `UNPROVEN ATTEMPT ON RECORD` — therefore **cannot fire on…

### a_family_correctly_keyed_can_still_be_split_by_a_field_nobody_counts_as_key.md
*"loop_health rolls up recurrences on (category, sig) not sig, so one hazard wearing two category labels publishes as two smaller rows -- the x12-read-as-x3 defect one level up"*
2026-08-10, graphify-kl-daily-refresh. `_tools\loop_health.py` groups the RECURRING block on the pair **(category, sig)**. `friction.record`'s first argument is a free category and `sig` is optional, so the *same* hazard lands in two buckets depending on how much the recording lane knew:  - a lane that has never seen the sig list picks the hazard NAME as its category —   `scratchpad-invisible-to-t…

### a_file_named_preimport_was_written_after_the_import.md
*"2026-08-04 — moat_preimport_20260804T071011Z.dump restore-verifies at the POST-import count; nightly 03:09 local vs Tuesday weekly import 03:07 local is a ~3min margin, and _shepherd_manifest.py picked by mtime"*
The nightly moat backup fires ~03:09-03:10 local; the Tuesday weekly rescore lands its import ~03:07 local. **On 2026-08-04 the import wrote at 07:07:30.241156Z and the nightly started at 07:10:11.455388Z — 2m41s LATER.** `moat_preimport_20260804T071011Z.dump` restore-verifies at `mcp_llm_axis_scores` **1,983,940**, the POST-import count; pre-import was 1,965,677. The word *preimport* is in the fi…

### a_fixed_defect_survived_in_the_copy_the_skill_mandates.md
*"FU-201 was repaired in _tools/fu_append_log.py and left defective in tools/append_fu_log.py — near-anagram filenames, and the SKILL points at the broken one"*
2026-08-07, graphify-kl-daily-refresh. FU-201 (ledger writer flips every line terminator) was marked **resolved** on 2026-07-30. It was fixed in `_tools/fu_lock.py` and `_tools/fu_append_log.py`. It was **never fixed** in `tools/append_fu_log.py` — and that is the one this lane's SKILL step B6 mandates.  Look at the two names:      _tools\fu_append_log.py     FIXED     (opens with newline="" both …

### a_fixture_that_omits_the_field_cannot_disagree.md
*"accept_gate printed \"31 services mounted\" while prod mounted 27, and 28 tests could not catch it because the fixture had no `mounted` key at all"*
`accept_gate`'s ACCEPT line read `(31 services mounted)`. It was printing `service_count` — the DECLARED total — and labelling it the mounted count. The live v65 `/spine/health` payload is `mounted[27] + skipped_no_router[4] + failures[0] == service_count 31`, so the number in the gate's own success line was wrong against every prod payload since v65, and `prod_deploy_state.json` faithfully re-rec…

### a_flat_count_cannot_tell_a_dead_wave_from_a_stranded_import.md
*"A count flat across a fired job looks identical whether the job stranded its output or never produced any — read the job's own verdict artifact, not the counter."*
Measured 2026-08-11 by `plan-200k-count-tracker`. `moat-rescore-weekly` fired on schedule (`lastRunAt 2026-08-11T06:04:37Z`, live scheduler) and `scored_servers` stayed at 283,420 — a flat count **spanning a fired wave**, which the lane's own 2026-08-03 rule labelled *evidence of a stranded import*. It was not one. Both waves died before the import phase existed: wave 1 pod FATAL on `fetch bundle`…

### a_fresh_mtime_on_the_graph_is_not_a_fresh_graph.md
*"The KL's graph.json mtime read 27 minutes old while the bus was 30 commits / 16.6h behind HEAD — a rewritten file is not a reloaded graph, and the daemon orphans its own builders"*
Measured 2026-09-01 by `graphify-kl-daily-refresh`. `graphify-out/graph.json` had an mtime **27 minutes old**. The bus (`:8772`, endpoint `/query`) reported `built_at_commit=60d858101f5b`, committed 2026-08-31T17:21Z — **30 commits and 16.6h behind** HEAD `130fef41d539`. The file was rewritten; the graph the loop reasons from had not moved. **A fresh mtime is a FALSE FRESHNESS reading.** The whole…

### a_gate_armed_on_the_commit_path_cannot_see_the_file_path.md
*"2026-08-04 — the hollow gate is armed at 3 seams, all on the COMMIT path; the promoter reads the WORKTREE, so 7 untracked hollow files no seam has ever seen still promote"*
**PR #2761 fixed the rule and armed it in three places — `goose_runner` (pre-`.done`), the publisher (pre-PR), and `tests/ci/no_hollow_scaffold.py` (pre-merge). Every one of those fires when a file becomes a COMMIT.** `promote_staged_to_active.py` enumerates `services/staged/` **on disk** and its pass verb is still `exit == 0`. So the gate and the promoter do not observe the same population.  Meas…

### a_gate_bound_in_absolute_counts_sets_a_hidden_minimum_cohort.md
*"2026-07-30 — score_validity refused 1,695 clean scores, then passed the SAME predictions padded to 21,695. The blocking bound was min_on_ladder_rows>=250, an absolute count. At the cohort's real 1.24% on-ladder rate that silently requires ~20,179 servers, so the never-scored lane could never clear itself. A count threshold on a RATE phenomenon is a hidden minimum cohort size."*
`score_validity`'s `maintainer_trust` declared exception (FU-108) has four bounds. Three are ratios or shapes. One, **`min_on_ladder_rows >= 250`, is an absolute count** — and it was the only bound that ever mattered.  Proof, from splitting one run's own predictions by cohort (`_fu108/gate_forensics_wave3.py`, run `20260730-001738`):  | cohort | share | distinct | bits | on_ladder | verdict | |---…

### a_gate_that_is_permanently_red.md
*"--check-open-runs exited 1 on EVERY invocation for 60h over two $0 runs. The guard read the ledger; the operator wrote state.json. FU-132 / PR #2174."*
`weekly_rescore.py --check-open-runs` exited 1 on **every** invocation and would have forever. Runs `20260725-170556` and `20260725-181359` were killed deliberately at `preflight` — read-only: no export, no bundle, no instance, $0.00. The operator's last word went into each run's own `state.json` as `result: killed_*`, never into the ledger's `wedge_*` / `manual_*` abort vocabulary, which is the *…

### a_gate_that_runs_nowhere_that_merges.md
*"The dockerfile-copy-list test — the guard for the class that killed 7 prod services — was in no workflow at all; it ran only in the tower verifier. #2069."*
`tests/test_dockerfile_copy_covers_active_services.py` is the guard for the FU-102 class (a service registered in `services/active/` with no Dockerfile `COPY` → `ModuleNotFoundError` in prod, 7 of them live on v64 since 2026-07-25). It existed, it passed, and on 2026-07-27 it was discovered to be in **no GitHub workflow**. It ran only inside `tools/verify_deploy_candidate.py` on the tower — i.e. o…

### a_gate_that_skips_reports_as_a_gate_that_passes.md
*"promoter's contract-FAILED bucket went 43→0 because the contract RAN ZERO TIMES — a static gate short-circuited it. Zero measurement reads exactly like a clean sheet."*
2026-07-28: `promote_staged_to_active.py` observe-mode reported **98/98 HOLD** with `contract FAILED: 0` — down from 43 the day before. That is **not** the liveness gate starting to pass. It is the liveness gate **never executing**.  The promoter runs `_run_contract()` only `if not reasons` (~line 205), and otherwise records `contract_ok=None, contract_detail="skipped (static gate failed)"`. Every…

### a_gate_whose_only_honest_verb_is_excluded_can_never_go_green.md
*"C4 — the sole remaining Phase-2 gate — became unreachable by construction the moment its own correct anti-gaming fix shipped (FU-186, 2026-07-30)"*
**FU-186, filed 2026-07-30 by prod-drift-sentinel.** After the chairman's 2026-07-29 ruling closed C3, the capability state is **C1 MET · C2 MET · C3 MET · C4 0/10 · C5 per-fire** — C4 (shadow agreement, 10 total / 8 consecutive) is the ONLY thing holding prod auto-fire.  **C4 can never be met while Phase 1 binds.** [[FU-184]] correctly made `--would-fire` a closed enum and excluded `blocked` from…

### a_gentler_alternative_does_not_change_the_gradient.md
*"FU-180 asked for a third verb so a supersede could be NAMED correctly; the real defect was that the dangerous verb was the cheapest to reach for, so the fix had to make it COST evidence"*
FU-180 (fixed 2026-07-29, PR #2297, squash `8cff30d8`) asked for one thing: a third `shadow_decision --reconcile` verb, so that "the staged candidate was abandoned" could be recorded as something other than `--held-sha`, which sets `task_would_fire_human_held` — the one disagreement that blocks C4 and zeroes the consecutive run.  Shipping only what the FU asked for would have left the defect stand…

### a_glob_ran_a_sibling_sessions_writer_against_the_live_ledger.md
*"Resolving a helper script by filename glob executed a DIFFERENT session's non-idempotent ledger writer, duplicating three FU log lines — invoke by absolute path and print __file__"*
On 2026-08-01 `deploy-runtime-from-main` wrote a `ledger_write.py` into session scratch, then — because the path the file tools report is **not** the path Windows sees — reached for `Get-ChildItem -Recurse -Filter ledger_write.py`. That tree holds *every* session's scratch. The glob matched a sibling task's (`mcplookup-nightly-db-backup`) copy written 31 minutes earlier, and PowerShell ran **that*…

### a_green_prs_checks_grade_a_tree_that_may_no_longer_exist.md
*"PR #2058's 20 green checks ran 7 days before merge; main had since gained the FU-158 fix that the PR would have silently reverted, and git still said MERGEABLE"*
PR #2058 (goose-canary guards) was opened 2026-07-27 with 20 green checks and merged 2026-08-03 as squash `6ef5a641`. In between, FU-158 landed on main (#2287): a module-level `sys.exit()` in `directive_mcp.py` aborts pytest COLLECTION, because `SystemExit` derives from `BaseException` and the test guards say `except Exception`. **#2058's own new `mkdir` handler had re-introduced that exact shape …

### a_green_whose_count_did_not_move_ran_none_of_your_tests.md
*"The required pytest check is a 37-file allowlist; no test_rescore_*.py was ever in it. 577 passed with 10 tests added. Fixed PR #4365 -> 635."*
PR #4365's first push added **10 tests**. The required `pytest` check went GREEN reporting **`577 passed`** — byte-identical to 2026-08-31's count. `.github/workflows/evaluator.yml` runs an **explicit 37-file allowlist**, and none of the five `tests/test_rescore_*.py` files had ever been in it. Every change to the harness that rents paid GPUs and writes the production moat had merged on a green th…

### a_green_wrapper_can_skip_the_fix_entirely.md
*"ensure_proxy() returned OK in 0.0s and proved nothing about #2179 — a sibling's live tunnel made it early-return before the hydration line. Probe the helper, not the wrapper."*
To verify a merged fix, call the HELPER it added, not the wrapper that calls the helper. A wrapper with a fast path can return green without ever reaching the new code.  2026-07-28: `weekly_rescore.ensure_proxy()` returned `OK in 0.0s` when probed for PR #2179 (FU-151, AgentVault flyctl credential hydration). It proved nothing — a sibling lane's `flyctl proxy` (PID 19732, port 15432) was already b…

### a_guard_keyed_on_whole_command_substrings_flags_its_own_documentation.md
*"FU-341/cycle-0043: a substring-pair hazard test was blind to its subject's other spelling, disarmed by a stray token anywhere in the command, and fired on prose naming the family. Ask a guard about PATHS, not substrings. Also: is_file()==False means absent OR invisible."*
`scratchpad-invisible-to-tower` had a hazard entry and constructors since 2026-08-04 and still bit **8x across 5 lanes in 7 days**. The entry's test was:  ```python lambda c: "local-agent-mode-sessions" in c and "mnt" not in c ```  Three defects, all measured before the fix (`probe_scratchpad_classify_20260813.py`, 3 pass / 4 fail), none visible by reading the regex:  1. **Blind to its own subject…

### a_guard_on_the_path_that_runs_second_is_not_a_guard.md
*"threat_intel_ingestor's retry loop was correct and unreachable — the priming cycle() sat outside it, so one NULL url killed the OSV lane for 54 days across 184,818 spawns (FU-187, PR #2405)"*
`threat_intel_ingestor.py::run()` called the priming `cycle()` **outside** the `try/except` that protects every later cycle in the poll loop. The recovery was written correctly and sat **one line too late**: the process had to survive the unprotected first call to reach its own protection, so a deterministic first-cycle exception was terminal — and the supervisor's only remedy, restart, re-entered…

### a_guard_that_can_only_go_red_is_equally_broken.md
*"accept_gate's ACCEPT (rc 0) path was never asserted — a gate incapable of accepting would have rolled back a healthy prod deploy (#2295, FU-176)"*
`tools/accept_gate.py` decides whether a fired prod deploy STANDS or gets rolled back. Its 22 tests pinned `main()` for **REJECT (1)** and **ERROR (2)**. **Nothing pinned ACCEPT (0)** — the only rc that lets a healthy deploy live. `evaluate()` and `poll()` were asserted to *return* ACCEPT, but a returned value and a process exit code are different channels and only one of them acts.  **Why:** we h…

### a_guard_that_cannot_catch_what_it_guards.md
*"except Exception cannot catch SystemExit; the guard was present, deliberate, and structurally unable to fire — and its sibling already knew (FU-158, PR #2287)"*
`tests/test_directive_done_dedup.py` wrapped its `exec_module` of `zo_sentinel/mcp_servers/directive_mcp.py` in `try/except Exception:` — the author saw the risk and defended against it. But `directive_mcp.py` called `sys.exit(2)` at import, and **`SystemExit` derives from `BaseException`**, so the guard passed it straight through. pytest died at COLLECTION with `INTERNALERROR`: **178 of 969 tests…

### a_guard_that_polices_probes_refused_every_well_formed_probe.md
*"peer_review's vacuity filter did endswith() on a whole --attempt-file body, so a correct two-sided probe (which ends on its failure branch) was always refused"*
FU-296, 2026-08-09. `peer_review.py --falsify` refused a probe that had **just been demonstrated returning rc=0**, saying "matches the known-vacuous pattern `sys.exit(1)` -- it cannot return 0 for any input".  `_looks_vacuous()` was written for a ONE-LINE command and does `c.endswith(v)`. With `--attempt-file` it is handed the whole FILE BODY. A correct two-sided probe **necessarily ends on its fa…

### a_guard_was_defeated_by_the_residue_of_its_own_defect.md
*"FU-289 — BASE.exists() is CWD-relative on POSIX, and the FU-278 write half had mkdir'd a ghost dir of exactly that name in the mount root, so the new guard passed and returned a silent zero"*
2026-08-08. A sibling lane executed the CLEARED decision `peer-review-status-must-not-report-a-silent-zero` and added `if not BASE.exists(): sys.exit(2)` to `_tools/peer_review.py`. **It did not work.**  `BASE = Path(r"D:\zo\Zocomputer Agents")` is **not absolute on POSIX** — it is a single relative filename, so `.exists()` is resolved **against the CWD**. The *write* half of FU-278 had already mk…

### a_guard_whose_input_evaporated.md
*A guard can regress with its code untouched — the fire ceiling went UNEVALUATED (not RED) because a hand-rebuilt state file dropped the one key it reads*
FU-230 (2026-08-02T04:47Z) made `max_fires_per_24h` queryable in `_tools/authority.py`. Its sole input is `prod_deploy_state.json["last_prod_fire_utc"]`. **Seventeen hours later the key was gone**, and `--may prod_deploy_fire` answered:  ``` RATE: CEILING UNEVALUATED (max_fires_per_24h=1): could not resolve the last occurrence -- prod_deploy_state.json has no 'last_prod_fire_utc' field. ```  Cause…

### a_guard_with_a_floor_and_no_ceiling_cannot_see_a_land_grab.md
*"Falsified scratchpad-key-the-existing-family — its verify asserted >=4 rows with no upper bound, so a catch-all alias swallowing all 69 unkeyed rows passes green"*
2026-08-07. As adversary I FALSIFIED `scratchpad-key-the-existing-family` (filed by deploy-runtime-from-main). Its verify predicate was genuinely good — **unguarded, red before the change**, keyed on the correct existing family, with a no-shrink check over every already-keyed family. It still failed on one word:      if not (len(fam_rows) >= 4 and len(lanes) >= 4): ok = False  **A floor, and no ce…

### a_halt_outlived_the_condition_that_justified_it.md
*"2026-08-05 FU-236 — the lane's one blocked act was blocked \"while FU-231 is unanswered\"; FU-231 had been peer-cleared and ACTED 23h earlier by this same lane, so the halt was inherited, not required"*
**2026-08-05.** The autopoiesis lane's prompt named exactly one act it could not perform — wiring the hollow predicate into `promote_staged_to_active.py` — and scoped the block **conditionally**: *"while FU-231 is unanswered"*. The prompt also said, correctly, "check each run whether it still is."  `peer_decisions.json` showed `fu231-first-cohort-peer-clearable` at **`state=ACTED`**, filed by **th…

### a_hand_written_fixture_ages_out_the_moment_a_control_is_added.md
*"FU-262 — the floor probe policing \"inspected != running\" was itself grading a 4-row hand-written corpus; a control added hours earlier fell outside it, self_test returned 2, and the probe blamed a deadlock that was not back and stopped the whole loop"*
2026-08-05, improvement-loop cycle-0006. `improve_loop --select` returned **FLOOR RED (probe_darkloop_deadlock rc=2)** and the probe printed **"DEADLOCK IS BACK."** It was not back: in the SAME floor run `dark_tools.py --self-test` was **rc=0 on the live corpus**. Two members of one floor disagreed because they read two different corpora.  The probe monkeypatched `dark_tools.scan` to a **4-row han…

### a_hazard_family_label_hid_that_three_of_four_bites_were_one_call_site.md
*"ps-command-nested-quotes looked like a fleet-wide shell problem; reading the four ledger rows showed 3 of 4 were the same command, peer_review --propose"*
A recurring-hazard family name is a CATEGORY, and a category invites a category-shaped fix (better quoting discipline, another warning in the prompt). Before accepting that framing, **read the individual rows and count the CALL SITES.**  `ps-command-nested-quotes` (improvement-loop cycle-0018, 2026-08-07) presented as 4 stalls across 3 lanes — a fleet-wide PowerShell problem. The four rows said ot…

### a_hazard_family_label_hides_the_call_sites_it_is_made_of.md
*"Grouping friction by family label made one recurring hazard unfixable for 3 weeks; grouping by the COMMAND that was cut split it into a working recovery and a real orphan, and named the one tool of three that never adopted the shared constructor."*
2026-09-02, cycle-0063. `mcp-timeout-orphan` had 12 hits across 8 lanes and would not stop, despite a hazard entry, a documented fix string, and a shared constructor (`friction.self_detach`) since cycle-0038. cycle-0057 read the FAMILY LABEL, found the cure already existed, and closed UNRESOLVED with nothing changed.  Grouped by **the COMMAND that was cut**, the same rows split in two:  - `lane_st…

### a_hazard_guard_matched_one_of_its_own_five_ledger_rows.md
*"FU-348 — a guard's coverage of its OWN family is a number nobody computes; replay the ledger rows through the live test before trusting it."*
**cycle-0046, 2026-08-13.** `tee-floods-mcp-result` shipped 2026-08-07 with `test = python ... | Tee-Object` and went straight into the improvement loop's FLOOR. Replayed against its own five ledger rows six days later: **it matched 1 of 5.** The other four were the same hazard entering through the READ door — `Select-String` over FOLLOWUPS.md (129,496 chars), reading a 913-line `.out` back whole,…

### a_hazard_written_in_a_task_prompt_cannot_reach_another_lane.md
*"2026-08-06 FU-251 recurrence — the Start-Process spaced-path trap was already recorded in discovery-harvest-daily on 07-29, and still silently no-opped prod-drift's MANDATED first call a week later, because a task prompt is a per-lane surface"*
**`Start-Process -ArgumentList` does NOT re-quote an element containing a space.** Measured 2026-08-06, same session, same args: `@('D:\zo\Zocomputer Agents\_tools\lane_start.py',...)` → python receives `D:\zo\Zocomputer` → **NEVER RAN, stdout log 0 BYTES**; pre-quoted `'"D:\zo\Zocomputer Agents\_tools\lane_start.py"'` → ran clean, 872 B, 2 findings. **Every** tool under `_tools\` has a space in i…

### a_healthy_heartbeat_from_a_process_that_imported_before_the_mutation.md
*"FU-349 - a daemon heartbeating \"alive\" proved nothing because it imported the module BEFORE the file was broken; check importability in a subprocess, and never trust a restart script's rc over the pid"*
**2026-08-22, FU-349, GH #3415 regressed six days after its hand repair.** `zo_sentinel/__init__.py` — a bare package marker by contract — was again an "Auto-emitted service package" import hub with 22 `from .X import Y` lines naming modules that do not exist. `python3 -c "import zo_sentinel"` was RED, so every `python3 -m zo_sentinel.*` entrypoint was dead **on next start**.  **Why:** the running…

### a_held_candidate_expires_at_the_apis_answer_size.md
*"fire_gate hit GitHub's 300-file compare cap and went ERROR — holding a deploy candidate stable had a silent expiry nobody had named, at the size of an API's answer"*
2026-08-01T10:52Z. `tools/fire_gate.py` returned **ERROR rc=2** on the held candidate `ae71dafd`: *"compare returned 300 files, at or over the GitHub cap of 300"*. The FU-160 cap guard firing for the first time since it was written.  **The defect was not in the gate — it was in the interaction between two correct things.** The discipline is to hold one vetted candidate stable across many stages (`…

### a_helper_the_caller_never_heard_of.md
*"#2059 shipped the canonical writer for ops_audit_state.json; the SKILL step that writes it was never repointed, so the next run clobbered the history it was meant to protect."*
Shipping a helper does not land a fix. #2059 (2026-07-27) made `tools/ops_audit_state.py` the canonical owner of `D:\zo\runs\ops_audit_state.json` — schema v2 `entries[]`+`credits[]`, same-date upsert, `since_funding()`. Step 4 of the `vast-jobs-daily-audit` SKILL still said *"Persist {date, balance} into D:\zo\runs\ops_audit_state.json (create if missing; keep history)"* and named no helper. The …

### a_hold_recorded_as_blocked_is_a_timer_not_a_hold.md
*"When a merit reason AND a permission/pacing block both apply, record the MERIT one — `blocked` expires with the window it names and silently becomes a fire instruction"*
On 2026-08-03T00:47Z prod-drift-sentinel held candidate `de79e43a` at the moment **every gate the envelope names was green at once**: `--may prod_deploy_fire` ALLOWED, 8/8 verification gates PASS 0 skipped, `sha_green` rc=0 7/7, backup 17.7h restore-verified + off-site, rollback anchor proven pullable, Dockerfile blob byte-identical to the one deploy-compat last actually built — and, nine minutes …

### a_hold_worded_on_merit_is_what_lets_the_next_run_fire.md
*"2026-08-05 v70 closed 49 commits of drift; the enabling act was the PREVIOUS run wording its hold on merit instead of `blocked`, and Class A measured at the TIP rather than carved out as a prefix."*
**2026-08-05T00:56:50Z — v70 FIRED, prod drift 49 → 0, `accept_gate` ACCEPT rc=0.** prod `4a1d508a` (v69) → `3bc66c7b` (PR #2821).  **The whole delta was Class A *at the tip*.** `git rev-parse <sha>:migrations` was `7615052f89fa72f6b46afacd30343a362d934bd9` at BOTH ends, so `alembic upgrade head` was a no-op and FU-235 had no input anywhere in the 49 commits. This is the shape the 2026-08-03/04 ru…

### a_job_name_is_not_a_step_and_one_log_is_not_an_attribution.md
*capmap-check was blamed on tools/pull_check.py from one job log; a census of 38 branches attributed ZERO failures to it — the real steps were generate_spine --strict (21) and check_service_manifests (15)*
FU-297, 2026-08-09. 106 PRs open, 90 non-green, `capmap-check` blocking 33. Reading ONE job log gave: "it runs `python tools/pull_check.py`, which prints `wrote /tmp/pull_check_capmap.json` and exits 1 with no diagnostic" — textbook FU-256, and a print-more patch was already scoped.  **It was false.** `pull_check.py` prints a full report and **exits 0**; that line is merely the FIRST line of a ste…

### a_key_compared_against_one_spelling_double_counted_a_payment.md
*"2026-08-06 FU-267 — credit dedup compared int id to argparse's str, so one $25 top-up counted twice and budget.level published a FALSE RED; the 25-test file written to police that module was never in CI's pytest allowlist."*
`tools/ops_audit_state.py::record_credit()` deduped top-ups on the RAW `--id`. Vast invoice `3148330` was stored by an earlier caller as **int**; the daily audit re-records it through argparse, which has no `type=`, so it arrives as **str**. `("id", 3148330) != ("id", "3148330")` → the same $25 funding event counted twice:      credits_ever  $25 → $50 · since_funding $10.23 → $35.23 · budget.level…

### a_lag_measured_across_a_dead_run_is_not_a_lag.md
*"FU-237's \"43.17h import lag\" spanned a $0 preflight kill, a 41h dormancy and a DIFFERENT run's landing; the true fire-to-land lag is 55 minutes, and the SLA margin it predicted at ~1 minute was ~42 hours"*
FU-237 (2026-08-03) predicted the 2026-08-04 wave would land at ~2026-08-06T01:16Z against a breach threshold of 2026-08-06T01:14:52Z — **a margin of about one minute** — and concluded the freshness flag "carries no information about health."  The arithmetic was `fire 2026-07-28T06:04:30Z -> newest_scored_at 2026-07-30T01:14:52Z = 43.17h`. Neither endpoint belongs to the same run:  - `2026-07-28T0…

### a_lane_can_merge_fixes_into_a_tree_it_never_reads.md
*"The daily-ops-audit lane ran from the SHARED tower tree, 188 commits behind origin/main — so every tooling fix it merged for itself landed somewhere it does not read. Check lane_worktree.py --check on your own path."*
Measured 2026-08-01. `D:\zo\zo-sentinel\zo-sentinel` (the shared tower checkout) was **188 commits behind origin/main, 0 ahead**, and `python tools/lane_worktree.py --check <path>` answers **`SHARED -- do not heal unattended`** for it. The `vast-jobs-daily-audit` SKILL ran every one of its commands from there. So #2059, #2178, #2291 and #2625 — four PRs this lane merged to fix *its own tooling* — …

### a_lane_drove_a_resource_a_daemon_already_drives.md
*"graphify-kl-daily-refresh retried a deferred graph_refresh and started a SECOND concurrent reindex against the go.sh daemon's own run; the idle gate holds no mutex against a second caller"*
2026-08-11, FU-061. `graph_refresh.py` printed *"STALE but a build is active -- deferring reindex"*. The lane measured the gate honestly (192 `build_artifact` rows/48h, median inter-arrival gap **7.5 min against the 8-min `GR_IDLE_MIN`**, 89 idle windows), correctly concluded the deferral was transient, retried at 10:06 — and put a **second** `index_graph` + `graphify update` onto the same 227k-no…

### a_lane_has_no_single_home_for_its_run_log.md
*"The deploy lane's '08-04 run log is missing a day' alarm was false — the run happened AND was written back twice, just under the FUs it advanced rather than under the lane's usual entry; a census scoped to one FU cannot see a lane whose log has no fixed home"*
2026-08-06, `deploy-runtime-from-main`. The 08-05 deploy line published a hole with a predicate attached: *"the reflog shows ff merges at 2026-08-04 09:10:30Z and 14:32:19Z and this entry carries NO 2026-08-04 line — if 2026-08-06's line lands and 08-04 is still absent, the gap is a **write-back failure in this lane** rather than a missed run."*  **Both halves of the discriminator came back GREEN …

### a_lane_was_silent_because_nothing_ever_told_it_to_check_in.md
*cycle-0019/FU-283 — improve_loop ranked ITSELF top silent lane; cause was not a broken task but that its SKILL never named lane_start.py. Fix = wire the obligation into the tool the lane cannot skip.*
**2026-08-07, cycle-0019.** `improve_loop.py --select` ranked `silent_lane / improvement-loop` at 85 — while running AS improvement-loop, in the session that produced the finding. `lane_start.py --audit` rc=1, `!! improvement-loop 36.0h ago`.  **The cause was none of the four the candidate lists** (disabled, usage-limited, erroring, silently skipping). The task was firing normally. `improvement-lo…

### a_launcher_fix_protects_only_its_own_child.md
*"FU-112's detached-launcher fix was applied to backup_zo_sentinel.py and to nothing else — offsite_push.py still ran in the foreground and was torn down mid-345MB-upload on 2026-07-29. It survived on ~60s of luck."*
FU-112 (2026-07-27) established the rule for this box: a long job run in the FOREGROUND from an agent/MCP shell **dies when the request times out**, because the parent shell is torn down and the child goes with it. The fix was `run_backup.ps1` — detached launch, per-run `.cmd` (every path here contains a space), the CHILD writes its own exit code to a file, the caller polls FILES instead of holdin…

### a_launchers_exit_code_is_not_evidence_its_child_ran.md
*"tools_cadence_fire.ps1 was authored as a fix AFTER the incident and never once ran; Start-Process -ArgumentList joins unquoted, so a folder name with a space made -File resolve to D:\\zo\\Zocomputer and the child died with exit -196608 while the launcher reported a PID and exit 0"*
A remediation script written *after* the incident it fixes may never have been executed even once. `tools_cadence_fire.ps1` (the detached-POST fix for [[FU-062]]'s ~60s MCP transport ceiling) had mtime **2026-07-29 06:30 EDT** — *after* that morning's 06:24 fires. Its first real invocation was 2026-07-30, and it produced **nothing**: no log, no marker, no surviving process.  **Why:** `Start-Proces…

### a_margin_called_structural_was_derived_from_one_sample.md
*"FU-237 called the freshness SLA \"zero margin by construction\" from ONE lag measurement; the second sample was 41x smaller and the real margin is +163h"*
On 2026-08-03 the plan-200k tracker filed FU-237: the freshness SLA is 7 days and `moat-rescore-weekly` fires every 7 days, therefore margin is zero **by construction** and the breach flag would alternate on import-lag noise. It predicted the 08-04 wave would land ~2026-08-06T01:16Z, breaching by minutes.  Measured 2026-08-04 (`computed_at 11:54:12Z`, `cache_age_seconds 43.5`): the wave fired 06:0…

### a_mechanical_hazard_can_fake_its_own_negative_control.md
*"2026-08-06 FU-267/FU-258 — a PowerShell text round-trip mangled the pre-fix module during an R4 negative control, so 4 tests failed exactly as predicted but with AttributeError instead of AssertionError; pass/fail alone was indistinguishable from the control succeeding."*
Running an R4 negative control, I restored a pre-fix module with `git show HEAD:tools/ops_audit_state.py | Set-Content -Encoding utf8`. PowerShell mangled it. The four tests then FAILED — **exactly the outcome the control predicted** — but with `AttributeError: module has no attribute 'record_credit'`, not the `AssertionError` they were written to raise.  **Why:** a negative control is the one pro…

### a_merged_correctness_fix_is_not_a_running_one.md
*"FU-031's PYTHONPATH one-liner merged as #2177 at 12:19Z; the runtime was 3 commits behind and goose_runner had been up since 05:01 — the fix was inert until safe_ff + reload_daemon."*
2026-07-28: the chairman merged the FU-031 lever (`#2177`, `7f32dda6`) at **12:19Z**. Four minutes later the tracker found the runtime checkout **3 commits behind** and `goose_runner` still running **pid 7924, started 05:01Z** — i.e. executing pre-fix bytes. Merging changed nothing on the box.  Arming it took two sanctioned steps: `ops/host/safe_ff.sh` → `7f32dda6` (12:22Z), then `tools/reload_dae…

### a_migration_cannot_be_additive_enough_to_stay_class_a.md
*"Class A/B is migrations-tree OBJECT identity, so ANY added migration is Class B — content-green (expand-only) and hazard-class are different axes and 0011's docstring conflated them"*
`prod-drift-sentinel` grades a candidate **Class A** (pure image swap, auto-fireable) vs **Class B** (attended-only, forever) by `git rev-parse <sha>:migrations` on the running sha and the candidate. **It is tree-OBJECT identity.** So adding any migration file moves the tree and the candidate is Class B. **Class A means NO migration at all.**  Measured 2026-08-02: prod `d5cb1d0f` tree `30dcd9fa` v…

### a_missing_pre_launch_side_effect_proves_never_started.md
*"FU-208 — the nightly backup didn't run and fail, it never launched; the absence of its pre-launch .cmd file is what distinguishes the two"*
`run_backup.ps1` writes four artifacts into `db_backups\logs\` — `backup_<RUNID>.cmd`, `.out.json`, `.err.log`, `.rc` — and it writes the `.cmd` and opens the redirects **before** launching Python. On 2026-07-31 the nightly moat backup produced no dump and `lastRunAt` said `07:10:01Z`. There was **not one artifact matching `backup_20260731*`** — no cmd, no zero-byte err log, no rc file — while eve…

### a_missing_tool_fails_loudly_a_stale_tool_answers.md
*"The shared runbook worktree had no owner; a sibling parked it at the candidate sha and accept_gate would have run 131 commits stale, silently — while the only guard fired for an ABSENT file"*
**2026-07-30, FU-198, PR #2415.** Every sentinel lane is told to run its tools "from `D:\zo\_runbook` (refreshed to origin/main)". That sentence names a **PATH**. Nothing refreshed it, nothing owned it, and any sibling may `git checkout --detach` into it.  At **13:39:49Z** something parked it at **`ae71dafd`** — the *staged candidate* sha, **131 commits behind main**. Ten minutes later `prod-drift…

### a_monitor_can_drift_on_itself.md
*"7/27: the FU-anchor drift monitor reported ITSELF as the biggest drifted FU — its standing-home FU quotes other FUs' anchors, so the report fed the index. Also: a per-FU cache that never prunes is reproducible but not convergent."*
7/27 graphify-kl-daily-refresh. Phase B jumped 49/49/6 → 50/50/**7**; the +1 was **FU-110, the drift monitor's own standing-home entry**, with 7 unresolved anchors, none of them its own.  **Why it happened:** `build_fu_index.py` harvests every `.py` named anywhere in an FU block. FU-110 is by charter where the daily drift line gets written — so the anchors it *reports as drifted* were re-read as a…

### a_monitored_item_that_can_never_clear_poisons_the_count.md
*"FU-027 was reported \"drifted against ssl.py\" — the CPython stdlib module named in its prose. No repo will ever contain it, so that drift row was permanent and could only be re-triaged forever."*
`build_fu_index.py` harvested `.py` anchors from FU prose with a regex, and FU-027's cold-path text names **`ssl.py`** — the Python standard library TLS module. The drift monitor then reported FU-027 as drifted against a module the repo will never contain: a row that could never clear, only be re-read and re-triaged every day. Fixed 2026-08-01 by dropping an anchor only when it has NO path separat…

### a_monitors_headline_number_was_mostly_a_benign_category.md
*"fu_graph_sync reported drift=28 by flattening two opposite meanings; 13 of 16 new pairs were tower-local and never indexable, so real graph staleness had nowhere to show"*
2026-08-03, graphify-kl-daily-refresh. `fu_graph_sync.py` put every unresolved FU anchor into one `unresolved` list, so its headline `drift=28` mixed two things with OPPOSITE meanings — and the benign one dominated. The KL indexes the zo-sentinel repo only; the ledger routinely anchors tower-local files (`zo_call.py`, `authority.py`, `_tools/*.py`, `fetch_secret.py`). 13 of that day's 16 newly-dri…

### a_negative_control_can_pass_vacuously_on_the_wrong_probe.md
*"the in-repo-but-unindexed probe was README.md, which the KL actually indexes — it resolved and never reached the classifier, so the control tested nothing in the one direction that mattered"*
2026-08-03. Wrote a negative control to prove a new `in_repo_unindexed` classifier could reach BOTH buckets (its real-run answer was 0, and a zero you cannot see go non-zero is not evidence). Chose `README.md` as the "in the repo tree but absent from the graph" probe. The KL turns out to hold a node for it, so the anchor RESOLVED and never reached the classifier at all. The control reported FAIL o…

### a_negative_control_demanding_sole_causation_dies_when_a_correct_sibling_lands.md
*"FU-362 / cycle-0058 — a probe that blinds its subject and demands the WHOLE corpus go silent turns RED when a second, correct arm is added; measure exclusivity BY BLINDING, and require the form to fire live or the pass is vacuous"*
**A NEGATIVE CONTROL THAT DEMANDS ITS SUBJECT BE THE *ONLY* CAUSE GOES RED WHEN A SECOND, CORRECT CAUSE IS ADDED.** R4 needs NECESSARY causation for at least one form, never SOLE causation for all of them.  2026-09-01, `probe_aggregate_wait_20260812.py` (in `improve_loop.FLOOR`) went rc=2 and stopped the loop selecting any ordinary cycle. Nothing was broken: cycle-0057 added `_chained_transport_se…

### a_passing_gate_can_still_report_a_failure.md
*"7/27 — smoke-ladder exits 0 with a spine mount ImportError inside its allowlist; the bug that would have sunk the prod deploy was in the gate's OUTPUT, never its exit code."*
2026-07-27, prod-drift-sentinel dry-run of candidate `84ca738d`. `smoke-ladder` **PASSED** (exit 0) while printing:  ``` ERROR:spine:SPINE MOUNT FAILURES (1 of 31): server_axis_scores_summary_router   ImportError("cannot import name 'MCPServerRegistry' from 'app.models'") ```  Tier 4 passes because the check is `runtime_failures_within_allowlist` — the failure is *tolerated*, not absent. Anything …

### a_path_component_is_not_a_directory.md
*"Counting services with `git ls-files | awk -F/ '{print $3}'` counted services/active/__init__.py as a 32nd service and nearly published the T2 milestone"*
2026-08-01: the autopoiesis tracker measured `services/active` tracked count as **32**, up from 31 held for five days. 31→32 is exactly the T2 transition (first autonomous staged→active promotion). It was false.  `git ls-files services/active | awk -F/ '{print $3}' | sort -u` splits **paths**, so `services/active/__init__.py` contributed `__init__.py` as a 32nd "service". Real service dirs = 31. C…

### a_path_legal_on_ci_can_be_unopenable_on_the_tower.md
*"FU-209 — a literal `<service_name>` placeholder committed as a directory name made every Windows checkout of main fail; CI stayed green because ubuntu allows the name"*
On 2026-07-31, builder PR #2497 committed `services/staged/<service_name>/__init__.py` — the recipe placeholder from `goose_recipes/service_dir_from_exemplar.yaml` written **literally as a directory name** instead of substituted. `<` and `>` are reserved characters in Windows filenames, so from 06:36:57Z onward **no `git checkout` or `git worktree add` of `main` could succeed on the tower**: `erro…

### a_permission_restated_outside_the_grant_file_cannot_be_revoked.md
*"prod-drift-sentinel staged 20 times and never fired because deploy_prod.ps1's docstring still asserted a rule the chairman had retired by name 3 days earlier; drift reached 452 commits. Fixed by DELETING the second source of permission, not by adding a gate."*
**A grant restated anywhere outside the grant file is a latent stale-permission bug, and it fails CLOSED and SILENTLY — which is why it reads as caution instead of breakage.**  `authority.json` granted `prod_deploy_fire = {granted: true, mode: FIRE_ON_GREEN, phase: 2}` on 2026-07-29 and retired the old Phase 1 rule **by name and by lane** in `supersedes_prose[1]`. But `ops/host/deploy_prod.ps1`'s …

### a_permission_value_no_code_path_could_read.md
*"authority.py compared clause_disposition against one literal, so writing PEER_CLEARABLE would have read as HELD; and the fix passed 52/52 while no CLI could reach it"*
**2026-08-06, FU-265, chairman act.** `authority.json` still said `HELD` for `redefining_the_metric`, `irreversible_and_unverifiable`, `new_standing_credentials` two days after the 08-04 ruling made them peer-clearable. **The stale value was not the defect.** `_disposition()` returns any string the file supplies, but `may()` compared it against exactly ONE literal — `"DECIDE_AND_LOG"`. Writing `PE…

### a_plain_get_to_version_and_freshness_serves_days_stale_edge_cache.md
*/version and /freshness serve multi-day edge-cached snapshots to a plain GET; mimics a data drop; cache-bust to read live*
On 2026-08-11 the pipeline-watch's first pass read `mcprisky.io/freshness` and `/version` with a plain GET and got an **08-05 / 08-07 edge-cached snapshot** — registry_rows/never_scored came back 4465 LOWER than the 08-10 history, which reads exactly like a data-loss ALERT (check B: "a count dropped vs last history"). It was not a drop. A `?cb=<ts>` cache-buster returned the live values (registry_…

### a_precondition_no_surface_can_query_is_a_coin_flip.md
*"FIRE_ON_GREEN requires a restore-verified backup <24h; the evidence was in the manifest all along and no tool the sentinel calls could read it, so the lane nearly held a healthy deploy"*
`authority.json` makes **"restore-verified backup < 24h"** one of five FIRE_ON_GREEN preconditions. Nothing `prod-drift-sentinel` calls could evaluate it. `backup_select.py newest` — the SANCTIONED selector, named in the charter precisely so the answer does not come from a directory listing — returned `dir / layout / name_ts / manifest / manifest_exists` and no restorability signal. `offsite_push.…

### a_predicate_over_the_code_is_green_while_the_hazard_bites_daily.md
*"2026-08-06 cycle-0010 — improve_loop's top-ranked candidate was UNGRADEABLE by construction for its whole existence, and the obvious scoped predicate would have reported GREEN on a hazard biting 15x across 9 lanes"*
**The engine's highest-scoring class had never once been selectable.** `improve_loop.candidates()` hardcoded `friction.py --self-test` as the predicate for every `recurring_friction` item. That is a FLOOR member, and `choose()` correctly refuses floor members as UNGRADEABLE — so the candidate ranked **#1 on 08-05 and again on 08-06 and was skipped both times**, while `ps-command-dollar` went on bi…

### a_probe_that_diffs_a_shared_directory_has_no_ownership_key.md
*"FU-382 — a floor probe attributed every new file in the shared _friction_scratch to itself, and discarded the child output that would have named its own red"*
`probe_verify_detach_20260810.py` (FLOOR member of `improve_loop --select`) decided its three poles by `set(os.listdir(_friction_scratch))` before/after and treating every new `*.cmd` as its own. That directory is fleet-shared, so **any concurrent lane running improve_loop turned poles B and C RED** while improve_loop's behaviour was unchanged. Proven, not argued: a foreign writer dropping markers…

### a_probe_that_exits_zero_for_the_wrong_reason_reads_as_a_finding.md
*"A negative control silently passed because the scanner SKIPPED the fixture by name, and the false pass read as \"the gate is blind\" rather than as an error."*
2026-08-10, deploy-runtime-from-main. To satisfy R4 on the spine gate I injected `services/active/__negctl_probe__/` (no `service.toml`) into the negative-control clone and got **rc=0**. I read that as *the authoritative gate is blind*, which would have voided the deploy's green.  The gate was fine. `generate_spine.scan_active()` skips any entry whose name starts with `_` or `.` — that is how `ser…

### a_probes_hardcoded_cohort_would_have_gone_green_by_aging_out.md
*"2026-08-06 FU-231 — cohort_trackedness.py hardcoded a 12-service cohort; the live cohort became 13, so the probe was one run from printing \"12/12 tracked, rc=0\" while a live member sat at 2/5. Compare the SET, not the count."*
`_staging/cohort_trackedness.py` carried its cohort as a **hardcoded fixture snapshotted 2026-08-05** (12 services). On 08-06 the honest cohort became **13** — `definition_history` ENTERED, nothing left. Had the fixture not been refreshed, the probe would have reported `COHORT 12 | fully tracked 12/12 | rc=0` once PR #2938 merged: **a green meaning the fixture aged out, not that the gap closed**, …

### a_proof_whose_cost_scales_with_the_artifact_expires_as_a_refusal.md
*"FU-315 — peer_review --propose refuses rc=3 because the revert probe, unchanged and proven rc=0 yesterday, now full-clones a repo that outgrew the 60s cap"*
2026-08-10. `peer_review.py --propose` refused `enforce-first-cohort-max-per-run-1-v4` twice, identically, **rc=3**: `the revert command was not PROVEN RUNNABLE (probe rc=None, 0 required) ... UNKNOWN: timed out after 60s`.  **Nothing is wrong with the revert.** `revert_enforce_v3.py --probe` is unchanged and was recorded `revert_probe_rc: 0` on 08-09, where it demonstrated tracked active 31→32→31…

### a_proposal_proposed_the_move_its_own_target_artifact_forbids_by_name.md
*"A peer proposal asked to raise the ratchet baseline 277->335; the data file it edits refuses that exact move by name and number, six days earlier - read the artifact's OWN text before proposing or clearing"*
FU-370, 2026-09-01. `repin-reachability-baseline-to-live-census` (filed_by `daily-chairman-review`) asked to re-pin `tools/reachability_baseline.json` `orphan_count` from **277 to 335** and keep `--enforce`, arguing a frozen baseline had turned a DERIVATIVE gate into a LEVEL gate. It quoted that file's own note as its justification. **The same note, dated 2026-08-26, refuses the move by name and b…

### a_read_only_probe_written_as_an_import_destroyed_the_file_it_measured.md
*"2026-08-06 FU-268 — an arming probe imported ops_audit_state and called record_credit(state=..., no path); save() resolved `path or DEFAULT_PATH` and wrote the probe's partial state over the live file"*
**2026-08-06, score-import-shepherd, FU-268 / PR #2937 (merged `53282908`).** A section-6 arming probe for [[a_key_compared_against_one_spelling_double_counted_a_payment]] imported `tools/ops_audit_state.py` and called `record_credit(25.0, ..., state={"credits": []})` with no `path=`. Both `record()` and `record_credit()` ended `if path is not False: save(state, path)`, and `save()` resolves `Path…

### a_receipt_confirmed_in_print_was_erased_by_a_writer_that_never_saw_it.md
*FU-351/cycle-0049 — lane_receipts lost-update race; any shared JSON doc loaded before slow steps and saved wholesale erases concurrent writers; reload at WRITE time*
FU-351 (2026-08-23). `lane_start.check_in()` loaded the whole `lane_receipts.json`, ran ~35–90s of steps, then saved the stale doc wholesale. In the 08-22 scheduler backlog flush, daily-chairman-review printed "receipt written" at 21:31:44Z and graphify (loaded 21:31:31Z, saved 21:32:51Z) erased it — the lane then read 234h SILENT in every audit while its scratch `.out` held the receipt it had wri…

### a_recovery_sweep_loses_to_the_writer_that_manufactures_the_fault.md
*"FU-233's fix gave the RELEASE sweep eyes but left the WRITE side manufacturing the same false state; the entries returned in one day with higher counters"*
2026-08-02, PR #2689 made `release_stale_missing()` resolve artifacts from the tree, so a stale `missing_on_disk` quarantine could clear. It cleared. On 2026-08-03 the same three keys were back — `__init__.py` 11→**19**, `service.toml` 19→**15**, `router.py` 4→**5** — because `_quarantine_overdue()`, the WRITER, still probed `<root>/<basename>` and re-manufactured the identical false claim on the …

### a_red_check_that_runs_zero_tests_dams_the_whole_factory.md
*"2026-08-05 FU-256 — pytest was RED and EMPTY for ~16h on main and all 87 open PRs; cause was Mako 1.4.0 shipping a top-level tools/ that shadowed the repo's NAMESPACE package. Merge on the test COUNT, never the colour."*
**2026-08-05 (FU-256).** `pytest` reported FAILURE on main and on **all 87 open PRs** from 2026-08-04T20:35Z. The failure was `ModuleNotFoundError: No module named 'tools.image_ship_check'` during **collection**, which pytest reports as `Interrupted` + **exit 2** — so the job ran **ZERO tests** while reporting a failure. Every `pytest` red in the queue had stopped saying anything about the code it…

### a_remedy_is_only_a_cure_if_it_is_reachable_from_the_surface_that_was_bitten.md
*"15 of 16 friction.py hazards answered a bite with an in-process python API, unreachable from the shell prompt where the bite happens — the cure required committing the hazard to reach the cure"*
**FU-373, improvement-loop cycle-0060, 2026-09-01.** `loop_health.py` named three families recurring and live that day: `ps-command-nested-quotes` x11/6 lanes, `mcp-timeout-orphan` x9/5 lanes, `inline-interpreter-source` x7/4 lanes.  A census of **all 16** `HAZARDS` entries in `_tools/friction.py`: **15 answered a bite with an IN-PROCESS python API** — `friction.pyrun()`, `friction.ps()`, `frictio…

### a_repair_that_moves_a_value_inherits_what_is_wrong_with_it.md
*"FU-324 — the fix that stopped a census error being silent copied it into alerts[] and widened the reach of the prod password inside it"*
**When a repair moves a value to a NEW surface, it inherits everything already wrong with that value.** Ask "where else does this now go?" in the same review as "is this now loud enough?"  Measured 2026-08-11 (mcplookup-nightly-db-backup), on the fleet's own repair from the day before:  - `backup_zo_sentinel._dsn()` puts the prod superuser password INSIDE the psql command   line, so any subprocess…

### a_required_check_called_no_hollow_ran_the_wrong_one_of_two_predicates.md
*"FU-285 — strict=false means a required check's green is NEVER re-run when the gate changes; I merged 8 PRs on greens predating the rule, 2 of them hollow. Check-run age is part of a green's meaning"*
**The filename records my FIRST, WRONG explanation. Kept deliberately: the correction is the lesson.**  I claimed the required `no-hollow` job imported only `hollow_scaffold_scan` (root-level `.py`) and was blind to service members. **Falsified by my own control** — on `origin/main` `hollow_scaffold_scan` already delegates to the service-member limb and rejects the blob. CI was never blind. **Run …

### a_required_context_can_be_satisfied_and_expected_at_the_same_time.md
*"FU-366 — builder PRs were BLOCKED because pull_request workflow runs died as action_required; approving only the named subset left a second `pytest` still EXPECTED"*
**2026-09-01, rob531/zo-sentinel.** `GET repos/rob531/zo-sentinel/actions/permissions/fork-pr-contributor-approval` → `{"approval_policy": "all_external_contributors"}`. Every `pull_request`-event run on a builder branch completes as **`action_required`** and never executes. Something already routes around this for `pr-gates` and `evaluator` via `workflow_dispatch` — which is why exactly those con…

### a_required_param_the_caller_never_passed_made_the_atomic_unit_unbuildable.md
*"FU-246 — goose_runner passed only task_description; the SOA recipe requires service_name+service_spec, so it failed 1082/1082 for 2 days while the engine fallback kept staged counts climbing"*
**THE SOA LANE HAS NEVER ONCE RUN.** `goose_recipes/service_dir_from_exemplar.yaml` declares `service_name` and `service_spec` as `requirement: required`. `run_goose_task` passed **only** `--params task_description=...`, unconditionally. That recipe is selected for **1082 of 1138** `[recipe]` lines (~95% of directives) and is the unit the factory was re-architected around ("the atomic unit is the …

### a_restriction_recorded_where_nothing_can_read_it.md
*authority.json carried max_fires_per_24h=1 for four days and authority.py had zero code referencing it -- --may returned ALLOWED 4h after the lane fired v66*
**2026-08-02, prod-drift-sentinel 04:47Z.** `authority.json.delegated.prod_deploy_fire.max_fires_per_24h = 1` had been in the envelope since the chairman's 2026-07-29 grant. `D:\zo\Zocomputer Agents\_tools\authority.py` — the sanctioned query surface, the file the charter tells every lane to consult *instead of* interpreting prose — contained **zero** references to it. `grep max_fires|24h|rate|las…

### a_revert_check_on_the_wrong_host_can_only_assert.md
*"FU-301 — three falsifications shared one unnamed cause: the checker ran on a host that could not see the artifact the revert acts on"*
`peer_review.py` runs tower-side (Windows). The `--enforce` promotion it was adjudicating happens in a git **worktree on the zo box**, which the tower has no path to — bridges 1 and 8 reach zo but neither grants git. So **there was no place on the tower from which that revert could honestly be demonstrated**, and every `revert_check` written for it was an assertion wearing a probe's clothes. The a…

### a_revert_trigger_named_an_outcome_word.md
*"\"'If it still fails, REVERT #2172' — but a timeout kill is reported as `cancelled`, not `failure`, and the 8m09s duration proved the fix had worked. FU-136/#2175."*
A prior run pre-committed: *"ACCEPTANCE IS THE NEXT SCHEDULED RUN: green with a nonzero triaged count. If it still fails, the fallback did not arm — REVERT #2172, do not patch forward."* On 2026-07-28 it still failed. **Reverting would have been wrong.**  **"Fails" collapsed three different failure modes into one word — and GitHub does not even call this one a failure.** pr-triage runs 30333992564…

### a_right_answer_can_carry_wrong_provenance.md
*"fire_gate printed the 100th commit of a paginated compare as \"the target head\" — verdict always correct, evidence wrong for six stages (FU-161, PR #2289)"*
`tools/fire_gate.py` derived its target head from the last commit of **page one** of `gh api .../compare/A...B --paginate`. Compare pages commits 100 at a time, so every delta over 100 commits named the **100th** commit as "main". Measured 2026-07-29: page 1 ended at `c1d9917e` (01:20Z), main was `77fd0b1b` (02:38Z).  The **verdict** was never affected — the `files` union across pages is complete,…

### a_run_can_do_the_work_and_leave_no_record.md
*"prod-drift-sentinel's 2026-07-29 07:47Z slot ran all 8 gates then wrote no state; write the receipt BEFORE the work"*
On 2026-07-29 the `prod-drift-sentinel` 07:47Z slot ran the full 8-gate verification and ended **without writing `prod_deploy_state.json`**. Its verdict artifact sat on disk dated `checked_utc 07:51:53Z` while the state file's `last_check_utc` still read `05:01:00Z`. Nothing reconciled the two.  **Why:** the durable record is written at the END of a run; the expensive evidence is produced in the M…

### a_run_that_did_the_work_and_persisted_none_of_it.md
*"zo-sentinel-pipeline-watch 08-08: a FOURTH cause of the empty slot — the run completed every check, then was cut at the persistence step it had ordered LAST. Artifact-side identical to 'fired and died'; only the transcript separates them."*
**A run can be 90% successful and leave 0% of a trace, and the artifact side cannot tell that from a crash-on-launch.** Measured 2026-08-08 on `zo-sentinel-pipeline-watch`, session `local_2956b2e0-4915-4a84-a292-caae05378a7c`: the 08:05Z slot completed checks A–G and every one was green (Fly 200, auth gate enforced, spine `31/27/4/0`, deploy `bf9e5de9`, and the ~48h `/freshness` recompute stall ca…

### a_run_that_never_happened_is_only_visible_as_a_hole.md
*"The 7/31 nightly backup was killed 5s in by a WEEKLY USAGE LIMIT; a missed run leaves no artifact, so only a GAP IN THE SERIES can find it — every \"is the newest one good?\" surface goes green again by morning"*
2026-08-01, `mcplookup-nightly-db-backup`. The 2026-07-31 07:10:01Z nightly session lived **five seconds** — its whole assistant turn was `You've hit your weekly limit · resets 3pm (America/New_York)`. 3pm ET = 19:00Z; the moat dump finally appeared at 19:24Z, taken by a *sibling* lane. **36.24h between restore-verified copies of the moat**, and the P0 that gated the staged prod deploy ([[an_open_…

### a_runbook_that_restates_a_value_goes_stale_at_the_moment_it_matters.md
*"deploy_prod.ps1 hardcoded \"release_command = 'alembic upgrade head'\" and printed it during the v69 fire, hours after #2775 made it false"*
`ops/host/deploy_prod.ps1` printed, immediately before deploying:  ``` [deploy_prod] release_command = 'alembic upgrade head' WILL run against the prod moat PG. ```  Hardcoded. #2775 changed `fly.toml` to the `$OWNER_DATABASE_URL` form, so the line was false from the moment that merged — and it printed, unchanged, during the real v69 fire.  **Why:** this is the one line an operator reads to learn …

### a_running_verdict_with_no_basis_cannot_be_told_from_a_hang.md
*"FU-342 — a detached python child buffers stdout, so .out reads 0 B for the whole run and every poll returns the same basis-free sentence; publish bytes WITHOUT unbuffering and you ship a false 'dead'"*
**Poll output that carries no measurement is silence with extra steps.** `poll_tag()` rc=3 used to print exactly `RUNNING: <tag> started <ts>, no .rc yet. Poll again (this is 3, not 0).` — byte-identical at second 2 and at minute 16, and byte-identical for a child making progress and a child wedged. Measured 2026-08-13 (cycle-0044): `improve_loop.py --select` ran **16 minutes with a 0-byte `.out` …

### a_scanner_taught_that_raw_text_lies_applies_it_only_where_it_was_bitten.md
*"FU-295 went three versions deep because each fix added string-literal awareness to exactly the one read that had just failed, leaving the sibling reads still parsing prose as code."*
2026-08-10, deploy-runtime-from-main falsified `fu295-adopt-string-aware-v3`.  The lineage of one predicate, `friction.record(...)` call sites carrying `sig=`:  - **v1** whole-file substring test. Falsified: its own remediation block   contained the key, so `--apply` turned it green while the unkeyed call site   survived above. - **v2** per-call-site, paren-matched. Falsified both signs: parens in…

### a_scar_cited_as_an_exemption_blinds_the_lane_that_quotes_it.md
*"cycle-0051 (08-23): pipeline-watch skipped lane_start 12 runs citing FU-278 \"tower-side only\" from its sandbox — a hazard memory read as a permission to skip. Scars must name the DOOR, not just the host class."*
Found 2026-08-23 (improvement-loop cycle-0051, silent_lane). zo-sentinel-pipeline-watch ran daily, wrote watch_result.json, and each run closed with "Self-steering (lane_start/peer_review/loop_health) is tower-side only per FU-278 — honest UNKNOWN from this sandbox." The scar was accurate ([[the_control_installed_to_catch_a_silent_stall_returned_a_silent_zero]]: run `_tools` WINDOWS-side) but the …

### a_schedule_change_is_a_code_change_to_every_tool_that_models_it.md
*"Cutting prod-drift-sentinel's cadence (right fix for FU-207) silently broke sentinel_run_ledger's hardcoded slot grid, which was about to email phantom MISSED SLOTS several times a day forever."*
On 2026-07-31 the prod-drift-sentinel cadence was cut from 8x/day (`:15` every 3h) to 4x/day (`45 0,6,15,20 * * *`) as the correct response to [[an-execution-record-is-not-an-execution]] (FU-207). Nothing was wrong with that change. But the cadence is an **input** to `tools/sentinel_run_ledger.py`, which hardcoded `SLOT_MINUTE=15 / SLOT_EVERY_HOURS=3`, and no edge connected them. Filed as FU-210, …

### a_set_keyed_on_a_shared_name_shrinks_the_population.md
*"Gate 8 cohort sizes were sets keyed on basename, so five services emitting __init__.py recorded as size=1 and tripped the breaker on a population of one"*
`gate_8_new_module._cohort_bump` and `_evaluate_file` did `t['size_files'].add(filename)` where `filename` was `Path(build['file']).name`. Because every service unit emits the same five filenames, a cohort of N services collapsed to **size=1**. That is the origin of the `cohort_15_n1: size=1 fail=100%` rows, and the breaker has read `tripped` since 2026-05-24 on that basis.  **A collided key does …

### a_seven_day_sla_on_a_seven_day_cadence_has_no_margin.md
*"2026-08-03: /freshness newest_scored_at is only advanced by moat-rescore-weekly (cron 0 2 * * 2), so a 7-day freshness SLA against a 7-day cadence breaches in the same minute the next wave lands -- and scores_rows is exactly 7x scored_servers, so it discriminates nothing."*
`plan-200k-count-tracker` step 6 flags `newest_scored_at` older than **7 days**. The only thing that advances `newest_scored_at` is a landed wave from `moat-rescore-weekly`, whose cron is `0 2 * * 2` -- also **7 days**. Measured 2026-08-03 from the SCHEDULER, not prose: fire `2026-07-28T06:04:30Z` -> `newest_scored_at 2026-07-30T01:14:52Z` = **43.17h of export+import lag**. Same lag on the next fi…

### a_shared_basename_is_a_shared_counter.md
*"The quarantine keyspace is a basename; the service era made basenames non-unique, so one service's failure gated every service — and the recovery sweep probed a path a service member never occupies."*
2026-08-02. `gate_quality_state` keys quarantine and retry counters on a **bare filename**. The file-unit era made that safe: every module had a unique name. The SERVICE era broke it silently — every service emits `service.toml`, `__init__.py`, `router.py`, `logic.py`, `contract.py`, so those five keys are shared by all ~330 services. One service's failures accumulate on a counter that gates *ever…

### a_shared_port_is_not_your_leftover.md
*"127.0.0.1:15432 is the rescore/import tooling's standing DSN convention, so the FU-057 broad-match proxy reap must never be promoted into a SKILL"*
`flyctl proxy 15432:5432 -a mcplookup-db` on the tower is **NOT** the nightly backup's leftover, and a reap keyed on `proxy 15432` + `mcplookup-db` does not select only one task's processes.  **127.0.0.1:15432 is the standing DSN convention for the rescore/import tooling.** Measured 2026-08-03, 23 files under `D:\zo\Zocomputer Agents` reference the port:  - `rescore_20260703\poll_import.py` — `psy…

### a_shared_pr_title_is_not_a_duplicate_pr.md
*"FU-288 — the 4 \"duplicate\" build PR pairs each touch entirely different files; a title-keyed dedup would have closed a 351-line router. Key dedup on the FILE SET"*
The chairman-review checklist says *close duplicate/churn PRs*. Grouping the 108 open `rob531/zo-sentinel` PRs by title gives 4 pairs — **none of them duplicates**:  | pair | one side | other side | |---|---|---| | #2911 / #2912 | 138-line `router.py` | 1-line `service.toml` | | #2838 / #2839 | **351-line `router.py`** | 1-line `service.toml` | | #2848 / #2849 | `cve_axis_freshness/router.py` | `c…

### a_single_force_loses_to_a_reconnecting_supervisor.md
*"DROP DATABASE WITH (FORCE) is a coin flip against flypgadmin, which reconnects to every DB within seconds; the FU-113 alert returning was not FU-113 regressing"*
`DROP DATABASE ... WITH (FORCE)` terminates the backends that exist **at the instant it fires**, then drops. A connection that ARRIVES inside that window still aborts the drop with `is being accessed by other users`.  On 2026-08-04 the nightly moat backup returned RC=1 DEGRADED on exactly the FU-113 alert — **with the FORCE fix present and running** at `backup_zo_sentinel.py:284`. Forensics before…

### a_slot_grid_is_a_claim_about_a_cron_that_lives_elsewhere.md
*"sentinel_run_ledger's SLOT_MINUTE=47 outlived a reschedule to `15 */3 * * *`, so every future slot would have reported MISSED — an email condition firing forever"*
On 2026-07-30 the run-ledger reported `MISSED SLOT 2026-07-30T16:47:00Z`, which is an **email condition** in the prod-drift-sentinel charter.  There was no 16:47 slot. The task had been rescheduled that morning to `15 */3 * * *` **LOCAL** (America/New_York, UTC-4 → 01:15Z, 04:15Z, …) to clear two long runners. The real slot was 16:15 and the run at 16:17:01 was two minutes LATE. `SLOT_MINUTE = 47`…

### a_state_nothing_writes_is_a_check_satisfied_vacuously_forever.md
*REVERT_FAILED read 0 for 19 days because nothing wrote it; CLOSED 2026-09-02 - the cause was PROSE INSIDE verify_cmd, which made the predicate uninvokable so three UNKNOWNs reached a terminal verdict with no RED. Grep the WRITE SITE of any state you enumerate; ask whether a stored command is a command*
FU-344, 2026-08-13. Roughly 17 zo-sentinel task prompts carry the instruction *"read the log of every `REVERT_FAILED`"*. That bucket has been **0** on every single sweep — not because the fleet has no failed reverts, but because **no code path in `peer_review.py` ever writes that state.**  The failed revert was hiding one rung down: `peer-review-status-must-not-report-a-silent-zero` has `state: AC…

### a_stray_temp_script_shadowed_the_stdlib_for_13_days.md
*A throwaway %TEMP%\inspect.py from 7/17 silently broke @dataclass for every agent helper script staged in %TEMP% on the tower; the traceback never named the cause*
**A helper script staged in `%TEMP%` runs with `%TEMP%` as `sys.path[0]`, so ANY `.py` left there whose basename matches a stdlib module silently replaces that module.** On 2026-07-17 some session wrote `C:\Users\robin\AppData\Local\Temp\inspect.py` (351 bytes, a regex dump of `weekly_rescore.py`). It sat there for **13 days**, shadowing the stdlib `inspect` for every Python file subsequently exec…

### a_thousands_separator_ate_the_list_delimiter.md
*"[int[]]\"63,64\" is 6364 in en-US culture — the launcher's documented multi-id call silently produced one phantom run id and watched neither real run"*
`tools_cadence_launch.ps1` declared `[int[]]$RunIds`. Under `powershell -File`, every argument arrives as a **literal string**, so the charter's own documented call `-RunIds 63,64` bound the string `"63,64"` to `[int[]]` — and PowerShell coerced it using the **current culture**, where `,` is a **thousands separator**:      [int[]]"63,64"  ->  6364  No error. No warning. One phantom watcher polled …

### a_threshold_that_lives_only_in_prose_is_not_a_guard.md
*"The $25/$20 budget RED line existed only as a sentence in the daily-ops SKILL — no code could compute or assert it, so the daily GREEN was an agent's arithmetic, not a measurement (FU-035, PR #2291, 2026-07-29)"*
The daily ops audit reported GREEN on spend every day for weeks. FU-035 spent three entries chasing why the **MTD metric** was fail-open. The real defect was one layer up: the threshold itself — "$25/month must last; alert at >=$20 spent" (chairman ruling 2026-07-17) — **was never code at all**. It lived only as prose in the scheduled task's SKILL. Nothing in `tools/ops_audit_state.py` computed a …

### a_tool_that_repairs_in_order_to_measure_throws_the_repair_away.md
*The promoter fixed 67 files on every observe run and discarded all 67; the highest-leverage act of the day was harvesting work that was already finished.*
**2026-08-10, daily-chairman-review.** `tools/promote_staged_to_active.py:212` calls `_linter.lint_file(..., fix=True)` **unconditionally, in observe mode** — deliberately, per its own comment: uncorrected `app.models` casing raises `ImportError` and the liveness contract cannot even run. So every observe run rewrote 67 staged files in the working tree, reported its verdicts, and **exited without …

### a_tools_wall_clock_can_tell_you_which_branch_it_took.md
*"The promoter's runtime collapsed from ~2-4min to <1s with no code change — that IS the signal that its liveness contract short-circuited for every candidate; runtime is a free discriminator for whether an expensive branch ran"*
2026-08-01, `tools/promote_staged_to_active.py` in OBSERVE mode finished in **<1s** (14:32:32Z → 14:32:33Z) against the ~2–4 min the tracker prompt had recorded at ~260 services. The reflex reading is "it got faster" or "it didn't run". Both wrong.  The tool runs its liveness contract only `if not reasons` (~line 205). All 300 candidates failed the *static* gate ([[the_promoter_wall_is_a_dockerfil…

### a_top_n_census_ranks_members_and_can_never_name_a_chain.md
*Seven of fourteen dark tools were ONE unwired pipeline; the census ranks members so three cycles each picked one and none could name the cause*
FU-304, improvement-loop cycle-0031, 2026-08-09. `dark_tools.py` ranked `tools/build_app_graph.py` top of a 14-tool dark list. Treated as one orphan it is a tidy-up. It is not one orphan: **7 of the 14 are the same unwired chain**, ~44 KB —      scan_capmap.py -> capmap.json -> build_app_graph.py -> schema/app_graph.sql                                   -> graph_gap_directives.py -> promote_graph_…

### a_trailing_window_predicate_punishes_the_lane_that_reports_honestly.md
*"FU-337 — 0 of 30 recurring_friction cycles have EVER been VERIFIED (53% of the engine's whole output); a fleet-wide trailing-7d predicate cannot be moved by a one-item-per-cycle engine. Peer proposal FILED 2026-08-31."*
**A predicate whose window already contains the failures it counts cannot grade a cure.** `friction.py --recurred mcp-timeout-orphan --days 7 --min 3` is RED while the family has >=3 hits in the trailing 7 days. At cycle-0042 selection it stood at 43 hits / 11 lanes with the last bite **0.0d ago**. A perfect fix and a total no-op produce the identical verdict for the next seven days.  **The measur…

### a_transport_sized_for_todays_payload_expires_on_a_schedule.md
*"The graphify Phase-B uploader died on the Windows 32,767-char command line the day the FU index reached 75 open FUs — a fixed ceiling under a payload that grows with the loop's self-description is a dated outage, not a bug."*
2026-08-02: Phase B3 of `graphify-kl-daily-refresh` base64'd the driver AND the FU index into ONE PowerShell command line. It died before a byte crossed to zocomputer:  ``` Program 'python.exe' failed to run: ... The filename or extension is too long ```  **Measured basis:** `_fu_index.json` 20,217B → 26,956 base64 chars; `fu_graph_sync.py` 5,429B → 7,240. Total 34,196 against the Windows CreatePr…

### a_trend_split_on_observed_days_reads_a_dormancy_gap_as_a_rise.md
*"loop_health's stall trend splits its window on days that HAVE rows, not calendar days, so a 6-day scheduler dormancy reads as a RISING stall rate and is the sole reason the tool exits rc=1"*
**FU-374, found by improvement-loop cycle-0060, 2026-09-01, deliberately left OPEN.**  `_tools/loop_health.py` lines 196-203 compute the stall trend over `by_day`, which contains **only days that have rows**. The median split is therefore over OBSERVED days, not calendar days. The trailing 14d ledger held rows on five days:      08-22: 9   08-23: 17   08-24: 11   [NO ROWS 08-25..08-30]   08-31: 27…

### a_ttl_artifact_with_no_writer_is_a_countdown_not_a_roster.md
*"FU-376 — lane_roster.json expired at 10.1d because nothing anywhere wrote it, and expiry silently collapsed every lane's cadence window to the 36h daily default, false-flagging a weekly lane that had run exactly on schedule."*
**Before trusting a red from a gate, ask what the gate's INPUT's TTL is and WHO WRITES IT.**  2026-09-01, cycle-0061. `lane_start.py --audit` went rc=1 naming two silent lanes. Neither was silent:  - `goose-shadow-research` is WEEKLY (`30 7 * * 1`), ran 37h earlier exactly on schedule,   and was judged against the **36h DAILY window**. - `probe-only` is a receipts artifact `_receipt_artifact()` al…

### a_type_confusion_returned_a_plausible_wrong_line_terminator.md
*"fu_ledger.line_terminator(str) iterates characters and returns \"\\n\" for a CRLF file — a wrong-but-plausible value instead of an error, which wrote bare-LF lines into the CRLF ledger."*
2026-08-06, plan-200k-count-tracker. `fu_ledger.line_terminator` is annotated `List[str] -> str` and works by asking each element `ln.endswith("\r\n")`. Pass it the whole file **as a str** and Python iterates *characters*: no single character ends with CRLF, but the first `"\n"` character does end with LF, so the function returns `"\n"` for a uniformly-CRLF ledger. It does not raise. It returns a …

### a_verification_step_that_cannot_fail_loudly_agrees_with_you.md
*"a PowerShell .Replace() that matches nothing returns the original string with rc 0 and no output; it silently no-op'd three times in one run and once nearly discarded a valid negative control"*
On 2026-07-30 the same mechanism produced a false green **three times in one run**: PowerShell's `.Replace()` (and an unasserted Python `str.replace`) returns the original string when the pattern does not match — **silently, rc 0, no output**.  - It failed to apply **mutant E** to `shadow_decision.py`, so pytest ran against the   UNMUTATED file and reported the pre-existing suite fully green. That…

### a_verify_predicate_can_name_a_module_nobody_built.md
*"FU-134/FU-149 both verify via _tools\\fly_auth.py, which exists nowhere - rc=2 degrades to UNKNOWN so no false green, but the FUs can then NEVER auto-close"*
Found 2026-07-29 by the graphify-KL anchor-drift phase (FU-110). Two open flyctl-credential FUs — **FU-134** and **FU-149** — both carry      verify: python "D:\zo\Zocomputer Agents\_tools\fly_auth.py" --check  and **that module does not exist anywhere**: not on the tower (`_tools\` is real and holds `fu_verify.py` / `fu_ledger.py` / `ledger_lint.py`, but no `fly_auth.py`), not on the container, n…

### a_verify_that_survives_success_cannot_witness_failure_to_act.md
*"FU-313/314 — a decision read ACTED with two GREEN sweeps while its artifact was never created, and the same command that was cleared would today promote the wrong service because selection is positional"*
2026-08-10. `enforce-first-cohort-max-per-run-1-v3` read **ACTED** (`daily-chairman-review`, 12:40:11Z) with two logged `verify GREEN` sweeps — and `git log --all --grep` for its own `PEER-REVERT-TOKEN` returned **0 hits**, tracked active dirs were still 31 (16th day), `cadence_job_sla_report` still 5 files in staged / 0 in active. **The promotion never happened and nothing could ever have said so…

### a_verify_whose_own_revert_falsifies_it_is_a_one_way_latch.md
*"FU-338 — a decision's verify asserted the state its own revert changes, so the first red pinned it red forever and silently re-armed a destructive revert for 12-19h"*
2026-08-12. `acted-needs-a-terminal-exit` (filed by autopoiesis-bar-tracker) installed `TERMINAL_COMPLETE` in `_tools/peer_review.py` and moved `enforce-first-cohort-max-per-run-1-v3` to COMPLETE, disarming a 7-file/6341-deletion revert ending in `git push origin HEAD:main` against the T2 artifact. Its `verify_cmd` (`_staging/acted_exit_probe.py`) asserted **that v3 was in COMPLETE** — the exact s…

### a_workflow_red_on_every_commit_never_ran.md
*"copilot-autofix startup-failed 1,919 times in 33 days, 0 jobs ever created — an invalid `on:` trigger, invisible because it never entered check-runs."*
2026-07-28: `.github/workflows/copilot-autofix-commit.yml` (added 2026-06-25, #687 "zero-click autofix") declared `on: code_scanning_alert`. That is a GitHub **webhook** event, **not** a valid Actions trigger. GitHub could not parse the file, so every push to every branch emitted a `startup_failure`: **1,919 runs, 0 successes, 0 jobs ever created.**  **The tell for a startup_failure:** `gh api ...…

### abandoned_mcp_call_still_writes_to_prod.md
*Windows-MCP abandons a call at ~60s but the shell KEEPS RUNNING — the queued POST landed 2min later and the retry made a duplicate prod run row.*
Windows-MCP's PowerShell tool aborts a tool call at **~60s regardless of the `timeout` argument you pass** — and the shell it spawned **is not killed**. It keeps executing. So every statement queued after the slow one still runs, minutes later, with no channel back to the caller.  Measured 2026-07-29 (cadence-jobs-daily-trigger): one call did `POST run-snapshots` → `sleep 3` → `POST drift-check`. …

### adapter_gitignore_garbage_scores_rootcause.md
*"ROOT CAUSE of 3 weeks of garbage scores + the RunPod-era 'weights keep vanishing' class: SFT repo .gitignore (*.safetensors/*.pt/*.bin) made `git add` SILENTLY skip adapter weights; I5 hashed the LOCAL copy so it passed green. 99.6% of scored moat is base+random-heads noise. Fixed FU-093 PR #1804."*
**THE defect** (2026-07-24, found only because the chairman asked "did we get our rescores?"): `ph_bundle` ran a plain `git add score_transfer`, but the SFT repo's `.gitignore` lists `*.safetensors`, `*.pt`, `*.bin` — so git **SILENTLY skipped the adapter weights + heads**. The pushed score branch carried only `adapter_config.json` + `README.md` (verified via GH contents API on `score-job-20260724…

### alert_suppression_needs_risk_axis.md
*Suppress a repeat alert only when the ASK *and* its RISK PROFILE are both unchanged — a silently-improved (or worsened) pending ask is new information.*
Duplicate-alert suppression must be gated on **two** axes, not one: suppress only when the **ask** is unchanged AND its **risk profile** is unchanged. An unchanged ask whose verified safety has moved is NEW information and must be sent.  **Why:** prod-drift-sentinel emailed the chairman at 05:15Z on 2026-07-27 staging `84ca738d` as a one-click prod deploy. That candidate would have fixed 6 of prod…

### an_abbreviated_flag_destroyed_the_write_and_returned_zero.md
*"argparse prefix-matched --sig onto --signature, whose branch ran first, so friction.py --record printed a reassuring family name, exited 0, and wrote no row"*
2026-08-06, improvement-loop cycle-0013, `_tools/friction.py` (FU-276).  `record()` grew an explicit `sig=` parameter on 2026-08-06 and its own stderr advice told lanes to "re-record with sig=<id>". No `--sig` flag existed and `main()` never passed one. A lane following that advice typed:  ``` friction.py --record <lane> <class> "<what>" --sig mcp-timeout-orphan ```  **argparse PREFIX-MATCHED `--s…

### an_absolute_path_does_not_pin_the_tree_it_runs_from.md
*"shadow_decision.py forwarded to a SIBLING lane's worktree because the caller invoked it by absolute path from outside any worktree — CWD-first resolution with nothing to resolve"*
**Measured 2026-08-04T00:59Z, both directions, same arguments, same second:**  | cwd | forwards to | resolved via | |---|---|---| | outside any worktree | `D:\zo\_lanes\clerk-sync\tools\shadow_decision.py` | `lane worktree` (LANE_GLOB fallback) | | `D:\zo\_lanes\prod-drift` | `D:\zo\_lanes\prod-drift\tools\...` | `git worktree enclosing the CWD` |  `_tools/shadow_decision.py` is **CWD-first by des…

### an_actuator_was_armed_on_a_report_that_nothing_ever_ran_again.md
*"The halt was armed on halt_shadow_report.py, which then had no caller; and the ARMED actuator's 26 alarm tests were never in CI's allowlist — a dark-tool census asks whether a tool is CALLED, never whether what it protects is TESTED"*
FU-305, improvement-loop cycle-0032, 2026-08-10. PR #3124 (fec100e9).  `tools/halt_shadow_report.py` ranked #2 dark. It is not dead code — it is a **decision instrument that was already spent**. `queue_census --halt-mode` has defaulted to **ARMED since 2026-07-30**, and the flag's own help says it was armed "AFTER the shadow report showed 0 halts firing today and the 7/29 founding case reproducing…

### an_adversary_that_cannot_read_a_proposal_rubber_stamps_it.md
*"peer_review.py had no --show and its usage block carried no help strings, so reading the proposal you are assigned to falsify meant rglob-ing the tree for peer_decisions.json — the read verb `--status --id <slug>` existed all along"*
`peer_review.py` assigns every lane an adversary duty, and until 2026-08-10 the cheapest way to READ the proposal you were assigned to break was to find `D:\zo\Zocomputer Agents\peer_decisions.json` by brute-force rglob. There is no `--show`; guessing it prints an argparse usage block that lists `--id ID` with **no help text at all**, so the failure teaches nothing. The read verb existed from the …

### an_assertion_never_seen_red_is_not_evidence.md
*My own guard test passed green with the invariant deleted — it matched a different line. Negative-control EVERY assertion by breaking the thing it guards.*
2026-07-27, PR #2066: I wrote `test_teardown_is_verified_not_assumed` to guard that `ops/host/verify_candidate.ps1` proves its worktree teardown. It asserted `"Test-Path $Path" in code`. That string **also** appears in the *pre-add* orphan heal — so the test stayed green with the post-teardown proof deleted. A placebo test guarding a placebo cleanup: the same error in two languages ([[cleanup_not_…

### an_auditor_can_omit_the_namespace_it_exists_to_check.md
*"kl_link_audit's docstring said \"resolve against EVERY store before calling a link broken\" and the tool then skipped the workspace-doc store — 6 of its 9 reds were its own blind spot, which is why rc=1 had become its steady state."*
`D:\zo\Zocomputer Agents\_tools\kl_link_audit.py` proves the Graphify join has no dangling edges. Its docstring stated the rule twice, with two dated measurements behind it: **"Resolve a link against EVERY store before calling it broken, or do not run the check."** It then resolved against three stores — the FU ledger, SPACES memory, MCP memory — and **not** against the store holding its single mo…

### an_execution_record_is_not_an_execution.md
*"lastRunAt advanced for 5 prod-drift slots that never reached step 0 and for a backup task that produced no dump — it records an ATTEMPT, not a result"*
**`list_scheduled_tasks`.`lastRunAt` is the scheduler's record that it ATTEMPTED an invocation. It is not evidence the run did anything.** Measured 2026-07-31: `prod-drift-sentinel.lastRunAt = 16:17:36Z` while `prod_deploy_state.json`'s `run_receipts` array still ended at `01:17:45Z` — five slots (04:15–16:15Z) fired and none reached step 0, the SKILL's first statement. Same minute, same shape: `m…

### an_idempotence_guard_matched_prose_and_reported_work_never_done.md
*"fu_append_log.py --if-absent 'cycle-0026' returned NO-OP 'already present' at rc=0 for a bullet never written, because the cycle id appeared inside an unrelated bullet's narrative text"*
`_tools/fu_append_log.py --if-absent <substring>` exists so a retried run converges instead of duplicating. On 2026-08-08 (cycle-0026) I passed the obvious sentinel — the cycle id, `cycle-0026` — and it printed **`NO-OP: 'cycle-0026' already present under FU-264'` and exited 0** for a bullet that had never been written. The string was sitting inside an unrelated 2026-08-06 bullet's prose.  **Why:*…

### an_in_flight_build_reddens_the_gate_that_grades_it.md
*"FU-195 negative control — an in-flight build materialises an EMPTY services/active/<name>/, which flips generate_spine --check --strict to rc=1; removed twice, recreated in 16s"*
2026-07-30. `services/active/server_search/` appeared with zero entries while `server_search` was still only STAGED and the promoter had reported `promote=0 / hold=178` three minutes earlier.  Negative control (both directions proven, which is what the sibling run had left open):  - `rmdir` at 12:27:20Z → `generate_spine.py --check --strict` **PROCESS rc 0**,   `verdict: CLEAN (services=31 broken=…

### an_item_that_left_the_population_is_not_an_item_that_was_fixed.md
*"The FU anchor-drift monitor's \"-10 cleared\" was ten pairs whose FUs had simply CLOSED — a set difference over a shrinking population always errs toward good news."*
2026-08-02, graphify-KL Phase B4. The daily anchor-drift diff is specified as a plain set difference over FU:anchor pairs — `NEW = today − last`, `CLEARED = last − today`. That day it reported **−10 cleared** across FU-199 and FU-208.  Nothing in the code graph had moved for any of the ten. Both FUs had been **closed** in FOLLOWUPS.md, left the open-FU index, and had their cache files pruned. The …

### an_observer_never_exercised_past_its_limit.md
*The cadence poller broke three ways at once the first time a run outlasted it — 64min vs an 11min norm. FU-140.*
On 2026-07-28 `perspective_snapshots` (run 54) ran **64+ minutes** against an 11m30s norm the day before. It was the first run ever to outlast the polling apparatus, and it broke that apparatus in three places simultaneously — none of which had ever fired, because no run had previously reached the length the poller exists to handle.  1. **The MCP transport caps a call at ~60s.** The script that PO…

### an_open_pr_is_a_claim_not_a_fix.md
*"The 14:00Z run logged status:resolved with two OPEN PRs as evidence; 3h later they were still green, clean and had changed nothing"*
prod-drift-sentinel's 14:00Z run on 2026-07-28 opened #2180 (FU-153) and #2181 (FU-154), wrote "ACTED — the deliverable, not a report", and set `status: resolved` in FOLLOWUPS.md. The evidence attached was two **open** PR numbers. At 16:49Z both were still `OPEN / MERGEABLE / CLEAN` with 15/15 real checks green, having fixed exactly nothing for three hours. The 16:49Z run merged both in seconds (1…

### an_unconditional_promote_turns_a_missed_run_into_evidence.md
*"The graphify baseline is promoted on EVERY run including silent ones, so a frozen baseline timestamp is positive proof a run never finished — the one surface that detects its own absence."*
The graphify-KL daily task promotes `graphify/fu_anchor_drift_last.json` on **every** run, including runs whose drift set did not change and which therefore write no ledger line. On 2026-08-01 the baseline was still stamped `2026-07-30T10:06Z` — which is not ambiguous. Because the promote is UNCONDITIONAL, a frozen timestamp cannot mean "nothing changed"; it can only mean **no run reached B5**. Th…

### an_unscoped_dedup_check_lets_a_stranger_veto_your_write.md
*"append_fu_log.py scanned the WHOLE ledger for (first-word-of-text, marker) as substrings, so an unrelated FU's bullet silently vetoed a write at rc=0"*
2026-08-04, `graphify-kl-daily-refresh` step B6. The **sanctioned** ledger writer `D:\zo\Zocomputer Agents\tools\append_fu_log.py` discarded a full run's finding and returned **rc=0** with a reassuring message.  The check was:  ```python stamp = a.text.split()[0]      # first word of the MESSAGE for ln in lines:               # EVERY line in a 3,780-line ledger     if stamp in ln and a.marker in l…

### anchor_exhaustion_no_novelty.md
*"ROOT CAUSE of the recurring 'no novel directives' problem: the architect's only 'what to build' signal (live_gaps_map) reads ONLY PRODUCT_SPEC.md candidate filenames minus disk; that v1.0 spec is fully realized → gaps=(none) → diagnostic churn. The real forward vision (SENTINEL_ROADMAP_v2 + addendum) is UNCONSUMED."*
**The recurring 'no novel directives' / no-novel-builds failure is an ANCHOR-EXHAUSTION problem, confirmed in code 2026-06-23.**  Mechanism (`directive_knowledge_sources.py`, repo root): - `assemble_layer1_context()` → product_spec, wiring_map, **gaps_map**, quality_map. The architect's ONLY "what SHOULD exist" signal is `live_gaps_map()`. - `live_gaps_map()` = `_spec_candidate_files(PRODUCT_SPEC.…

### anchor_genuinely_spent_confirmed_at_current_head.md
*"7/27 — the starvation floor's \"may just be a stale checkout\" hedge finally resolved: deployed to current HEAD, re-mined, 0 candidates. The spec extension is genuinely owed."*
The starvation floor's exhaustion message ships a discriminator ([[FU-032]], PR #1678) that hedges between two very different diagnoses: *"the anchor may NOT be spent; this checkout may simply predate the refill. DEPLOY FIRST (safe_ff.sh), then re-check before extending the spec."* Three prior occurrences were left unactioned because nobody ran the second half.  On 2026-07-27 the deploy task ran b…

### app_spine_needs_fastapi_recipe.md
*"The single-file builder ghosts on complex auth/RBAC/FastAPI modules; the ghost-guard fallback then writes compile-clean HOLLOW stubs that can reach triage:solid and auto-merge. The P0 auth/RBAC/OAuth spine needs the dedicated FastAPI recipe, NOT the generic generator. Proven 2026-06-24 on the 10 app candidates."*
**Proven 2026-06-24.** The architect wrote all 10 app-foundation candidates (14:01) and the builder built all 10. Outcome split by module complexity:  - **7 landed clean** (merged #543-549): product_audit_log, org_entity_search_api, tenant_org_model, verdict_watchlist_service, verdict_breakdown_api, entity_report_exporter, org_api_key_manager. CRUD/query/report-shaped — the single-file goose build…

### app_surface_kl_landed.md
*PR*
**PR #1722 MERGED** 2026-07-21 19:39Z, squash → `0d3d2d2`. Implements FU-071 (the KL-artifact pattern pointed at the surfaces beyond DB schema). Doc: `D:\zo\Zocomputer Agents\INTEGRATION_SURFACE_STRATEGY_2026-07-20.md`.  **What landed:** `app_surface_kl.py` (repo root, beside `schema_kl.py`), `tests/test_app_surface_kl.py` (12 tests), emission in `tools/graph_refresh.py` beside schema_kl, report-o…

### append_log_needs_newline_stripped_lines.md
*"fu_ledger.append_log inserts a bullet with NO trailing newline, so a caller holding the ledger as splitlines(keepends=True) silently swallows the NEXT key into its own bullet — it ate FU-103's 390-char resolution and the ledger still reparsed cleanly"*
`fu_ledger.append_log()` inserts `"  - %s" % text` **without a trailing newline**. The sanctioned caller therefore holds the ledger as newline-**stripped** lines and re-joins with `"\n"` — `fu_verify.py` does `lines = fh.read().split("\n")`.  Feed it `splitlines(keepends=True)` instead and the inserted bullet has no `\n` of its own, so it **concatenates with the line beneath it**. On 2026-07-30 th…

### appendix_d_app_baseline_2026_06_24.md
*Overnight DuckDB baseline for Appendix-D 3-tier app builds (PR*
OVERNIGHT BASELINE 2026-06-24 (~01:00–11:30 UTC), current DuckDB/write_service model, for post-Postgres-migration comparison. PR #518 added "Appendix D – 3-Tier App Foundation" (11 app-surface candidates: tenant_org_model, oauth_login_service, rbac_enforcer, verdict_breakdown_api, org_entity_search_api, overview_dashboard_api, entity_report_exporter, verdict_watchlist_service, org_api_key_manager,…

### architect_convergence_fixed.md
*2026-06-29 goose architect now CONVERGES + proposes real /app directives (cerebras + max_turns 24 + recipe force-toolcall); +0 era over*
2026-06-29: after retiring the legacy generator ([[legacy-directive-generator-retired]]), the sole goose architect (sentinel_directive_generator_goose.py, recipe directive_architect.yaml) was +0. Fixed end-to-end -- verified `proposed_depth 0 -> 2 (+2)` in 26s.  The +0 had THREE stacked causes, each fixed: 1. **Weak model.** Architect ran on zo-ladder-v1 / MiniMax-Text-01 (rung 0) -- over-explo…

### architect_converges_harness_discards.md
*"'ARCHITECT NON-CONVERGENCE +0' is a LIE — the model emits complete propose_directive calls in fenced ```python blocks and the harness throws them away, then rotates rungs. Prose-salvage 4th shape. FU-122."*
`sentinel_directive_generator_goose.log` logs `ARCHITECT NON-CONVERGENCE (zero_proposed) ... did NOT reach propose_directive (tool-call loop / over-exploration)`.  **Read the transcript tail directly above that line.** It contains complete, well-formed, semantically excellent directives — full `zo_directive_bridge__propose_directive(task=..., handler="build_service", description="... ACCEPTANCE: .…

### architect_dedup_and_reverse_feed.md
*2026-06-29 shipped a flag/sentinel-gated subtractive dedup filter (PR #1060, ENABLED) + staged the reverse-feed fetcher for ZoComputer (supervisord, not go.sh)*
2026-06-29 (chairman approved "ship the safe filter" + "stage a self-looping wrapper").  **Why past architect-dedup attempts reverted (from memory):** they touched GENERATION and starved the builder — architect going +0 (namespaced-tools / propose->promote "funnel-fork" regressions), generator jamming ~3 days / 47 dead proposals.  **This dedup is a different risk class (PR #1060, merged + depl…

### architect_namespaced_tools.md
*"THE +0 root cause (2026-06-23): goose-1.38 namespaces stdio-extension tools as zo_directive_bridge__<tool>, but the recipe listed BARE names, so every propose/read call failed -32002 tool-not-found. Fix = recipe lists the full namespaced names."*
**goose-1.38 namespaces every stdio MCP-extension tool as `<extension_name>__<tool>`** — so the directive bridge's tools are `zo_directive_bridge__propose_directive`, `zo_directive_bridge__read_gate_quality_state`, `zo_directive_bridge__list_domains`, etc. The `directive_architect.yaml` recipe listed the BARE names (`propose_directive`), so the model (Cerebras/gpt-oss-120b via the shim) called the…

### architect_starvation_overexploration.md
*"Architect +0 had TWO stacked causes: (1) recipe ordered read_* before propose (#1472), (2) openai rungs emit [TOOL_CALLS] as TEXT and the shim dropped it (#1474). Plus a code-level starvation floor (#1472/#1473/#1475) so the queue is NEVER empty."*
**Fixed 2026-07-14 (P0). Factory found DEAD: 0 proposed / 0 pending, builder idle 178 cycles, no build PR in 13h, architect +0 for a day.**  ## Cause 1 — the recipe contradicted itself (#1472, merged 5912619) `directive_architect.yaml` said *"PROPOSE EARLY — DO NOT OVER-EXPLORE… you usually do NOT need the read_* tools"* and then, immediately below, ordered: ``` WORKFLOW: 1. read_gate_quality_stat…

### ask_cannot_see_cves_and_the_cve_feeds_were_never_promoted.md
*"2026-08-06 — ASK's CVE matcher is unreachable because ask_corpus_indexer never emits CVE text (0 mentions), and the NVD/GHSA feed ingestors are STAGED not active; 92 of 426 staged services are CVE/vuln-related"*
**RETRIEVAL HALF LANDED 2026-08-24 — PR #3913 merged `a300123a` (daily-chairman-review).** The patch below was applied essentially as specified: chunk-scoped `_cve_ids_for`, `cve=` snippet segment + weighted `cve` terms field, emitted only for linked docs (hash stability for unlinked rows). Self-tested end-to-end with negative control. Merged ≠ armed: needs the prod deploy + an admin `POST /api/as…

### audit_cannot_judge_on_a_field_the_client_dropped.md
*"vast list_instances projected away label/start_date, so the 90min wedge guard lived only in SKILL prose and every run re-derived uptime with a raw API call — a legit rescore GPU and a real leak printed identically. PR #2059."*
`RealVastClient.list_instances()` returned `{id, state, dph}` only, discarding `label`, `start_date` and `status_msg`. The daily ops audit is REQUIRED to report uptime/status per instance and to flag `loading` past 90 minutes as a wedge (chairman ruling 2026-07-17) — **neither was reachable from the harness**. The guard therefore existed only as prose in the task file, re-derived by hand every run…

### autocrlf_false_is_only_safe_in_a_clone_created_under_it.md
*Setting core.autocrlf false in D:\zo\zo-sentinel\zo-sentinel makes ~77 clean files appear modified and turns any patch into a whole-file rewrite — that checkout was created under autocrlf=true.*
2026-08-02, daily-chairman-review. Following [[FU-225]]'s rule I ran `git config core.autocrlf false` in the tower checkout before my first file-tool write. **That rule is right for a fresh clone and wrong for this one.**  `D:\zo\zo-sentinel\zo-sentinel` was created under `autocrlf=true`: every blob is LF, every working copy is CRLF, and git was hiding the difference on read. Flipping the setting …

### autopoiesis_has_no_organ_that_reads_the_queue.md
*"Chairman 7/29: why can't a system built on autopoiesis catch this itself? Because every self-inspecting organ reads the DISK or the LEDGER. Nothing reads the OPEN PR QUEUE."*
Chairman, after the FU-120 fix landed: *"think about why a system built around autopoiesis can't proactively get this done."*  The answer is not effort, model quality, or missing autonomy. **It is a blind spot with a precise shape.**  Every self-inspecting organ the loop has reads one of two surfaces:  | organ | reads | so it sees | |---|---|---| | `generate_spine`, `pull_check`, reachability ratc…

### autopoiesis_nominal_action_baseline.md
*"Measured baseline for \"is this action nominal for autopoiesis\" — 58% of FU entries are agent-sourced; the risk is aggregate accumulation, not any single write."*
Measured 2026-07-27 when the chairman asked whether marginal out-of-loop actions carry cascading risk. Use these numbers instead of re-deriving them; re-measure rather than trust if the date is stale.  **What is nominal (FOLLOWUPS.md, the autopoiesis ledger):**  - 124 FU entries at time of measure — **72 (58%) agent/task-sourced vs 52 chairman-sourced**. Autonomous entry creation is the MAJORITY c…

### autopoietic_loop_naming.md
*"7/24 chairman doctrine — the ladder + architect-goose + builder-goose + gates assembly is named THE AUTOPOIETIC LOOP; Potemkin era = allopoietic; model substrate = protean (alias rungs, no vendor pins); use this vocabulary in docs/briefings/directives"*
Chairman (2026-07-24): the E2E ladder + arch-goose + build-goose needed a name that encapsulates the goal — an "almost regenerative living model." Adopted term: **AUTOPOIETIC** (Maturana & Varela: a system whose product is itself — it produces and maintains its own components, preserving organization while parts turn over). The model substrate behaves as a **PROTEAN** (no fixed shape, no hard bind…

### badge_trust_model.md
*"Badge/claim trust-but-verify spec — PR #1443; NOT ready; rug_pull_monitor revival is hard precondition"*
2026-07-12: chairman asked badge readiness + malicious-opt-in threat model. **Status: NOT functionally ready** — scorecard_badge_api.py (7/07) unmounted AND has zero freshness/kill-switch logic (needs rewrite, not mount); claim flow nonexistent; rug_pull_monitor DOWN (stale since ~6/20).  Spec shipped as **PR #1443** docs/DESIGN_BADGE_TRUST_MODEL_2026_07_12.md (VERIFY merged). Core stances: badge …

### builder_dead_61h_undetected.md
*goose_runner was down 61.6h (7/17-7/19) and three daily reviews missed it; absence has no detector*
goose_runner stopped after Cycle 586 at **2026-07-17T02:17:29Z**, resumed **2026-07-19T15:53:44Z** — **61.6 hours dead**. Three consecutive daily chairman reviews (7/18, 7/19, and 7/20 until 12:10Z) reported the factory healthy.  **How to detect it (the three-surface read):**  - `goose_runner.log` lines/day: 7/16=6098, 7/17=588, **7/18=0**, 7/19=1849, 7/20=2903 - `build_provenance` rows/day: 7/16=…

### builder_doubled_path_squatter.md
*"build_admin_ui_suite ghosts forever — stale pending/ directive declares output_file admin_admin_ui_suite.py; dedup is filename-only so corrected proposal can't supersede."*
`build_admin_ui_suite` can never pass the Tier-0 gate because its directive declares `output_file = admin_admin_ui_suite.py` (doubled `admin_` prefix). `declared_output()` in `build_completion.py` takes `output_file` verbatim, so the gate checks a path goose would never produce. The bad name comes from a **stale directive squatting in `directives/pending/` from 2026-06-04**; a corrected proposal f…

### builder_emits_two_service_toml_shapes.md
*"Disk says 133/140 fine. The QUEUE says 0/36. Every manifest the builder is currently emitting is unpromotable -- 53% of the open PR backlog. FU-120."*
## 2026-07-29 correction — the containment claim was FALSE  Census of all 140 `service.toml` on the tower (`D:\zo\_runbook\services\**`): **133 parse clean · 0 flat · 7 UNPARSEABLE.** The flat shape is gone. What replaced it is worse:  **Shape 3 — Python source in a file named `.toml`.** Five are `service = { "name": ..., "needs_data_layer": True, }` + `if __name__ == "__main__": print("PASS")`. O…

### builder_fix_engine_not_model_2026_07_01.md
*"2026-07-01 live verification of the builder-quality fix — outcome good, but the causal story (MiniMax bad / capable rungs good) is INVERTED; real axis is engine (builder vs goose)."*
2026-07-01: verified the builder-quality fix live against `build_provenance` (write_service `/query` on :8772, reached via zo gateway `bash`). Two-part finding:  **Confirmed (handover was right on OUTCOME):** #1101/#1103/#1105/#1106 (+autonomous builds #1102/#1111) all merged to `rob531/zo-sentinel` main. Routing fix is LIVE — `/app` builds now fan out across nvidia/mistral/cerebras + MiniMax inst…

### builder_idle_is_ghost_quarantine.md
*"2026-06-28 CORRECTION to next-session handoff — builder idle is NOT a complexity gate; it's a queue full of done/ghost-quarantined directives. goose_runner is current, not stale."*
2026-06-28: Investigated "builder looks stopped, skips all 19 directives as non-eligible (complexity=medium/low)". The handoff ([[next-session-handoff]]) framed this as an `is_goose_eligible` complexity gate to adjust. **That diagnosis is WRONG.**  Ground truth from the live box (`/home/workspace/zo_sentinel/`): - On-disk `goose_runner.py::is_goose_eligible` has **NO complexity gate** — docstring …

### builder_redirect_3tier.md
*"Chairman 2026-06-24 (session close): the autonomous builder is churning SIGNAL/ENRICHMENT/ENUMERATION modules that are REDUNDANT (the SFT student now owns risk scoring) and producing nothing toward the 3-tier app. Redirect: deprecate enrichment candidates from PRODUCT_SPEC, refill the anchor with 3-tier app milestones."*
**Chairman observation (2026-06-24, accurate):** no productive autonomous-build PRs for ~4h; the open PR set is all `*_enrichment` / `*_signal` / `diagnose_*` / enumeration; nothing advances the 3-tier architecture.  **Why:** earlier this session the architect built ALL Appendix-D app candidates (#543-549 merged) -> app surface in the anchor EXHAUSTED. The only remaining PRODUCT_SPEC candidates ar…

### builder_rescope_decision.md
*"2026-06-26 decision — re-scope the autonomous builder to exemplar-grounded bulk modules; integrated app/auth/UX is agent-built, not directive-built."*
2026-06-26 chairman reckoning: across one session a capable in-session agent hand-built the actual product (verdict API, wildcard search, dashboard, navigable nav-shell UI, Clerk JWT auth + RBAC + 20/day rate-limit) in hours, while ~142 autonomous builder PRs over ~6 months were ALL hollow (mock DBs, fake tables) — see [[hollow_scaffold_root_cause_recipe_schema]]. The ladder/directive/single-file-…

### builder_stall_2026_07_01.md
*2026-07-01 diagnosis — builder emission stopped + 16 hollow build PRs backlogged; both trace to ZoComputer down*
2026-07-01 (session start, tower-only, ZoComputer/zo MCP STILL DOWN): "builder stopped emitting PRs overnight" is TWO problems, both rooted in ZoComputer being unreachable.  **1. Nothing opened (emission halted).** Last autonomous `build:` PR was #1094 (verify_enrichment, 2026-06-30 11:36Z); last module build #1093 (06-30 06:26Z). ~24h silence. goose_runner runs ON ZoComputer; the container went d…

### builder_stall_quarantine_clog_2026_07_02.md
*"2026-07-02 builder stall was a quarantine/queue clog, NOT a dead daemon; reboot didn't fix it, clearing quarantine did. Cap 40 is a red herring. Pipeline dir topology captured."*
2026-07-02 ~02:30 UTC: "no PRs merging" after a `zm go`. Root cause = **queue clog, not process health.** All daemons were up (write_service, goose_runner PID30390, architect PID31398, proposed_to_pending_promoter, candidate_promoter_daemon) and goose fired 4 builds right after the 23:47 restart — then went idle ~2.7h. `zm go`/reboot did NOT help because the blockage was the QUEUE.  **Pipeline dir…

### bundle_push_pipeline.md
*"The bundle+push pipeline created 2026-05-25 — produces zo_sentinel_live.bundle and tower Push-ZoSentinel.ps1 for GH sync. This is the canonical sync path the GH-side E2E should build on, not be rebuilt."*
Created 2026-05-25 22:51 UTC. Path: `/home/workspace/logs/bundle_run.log` shows the end-to-end run.  **Outputs:** - `/home/workspace/shared/outputs/zo_sentinel_live.bundle` — 3.1 MB git bundle - Manifest JSON: `{"commit":"fafce62d376c424b6c28c681e7624f3531c529c1","py_files":755,"bundle":"zo_sentinel_live.bundle","created_at":"2026-05-25T22:51:31+00:00","tower_script":"shared/code/tower/github_push…

### cadence_jobs_outrun_10min_poll_at_463k.md
*"The 30min+ cadence overruns at 463K were BACKLOG, not scale — once the corpus caught up both jobs land in ~11min inside the poll window; long ≠ wedged, and the honest fallback is next-run /health"*
7/26: The mcprisky.io cadence trigger jobs (`perspective_snapshots` + `ask_corpus_drift`) now routinely run **30+ min** at 463,680-row registry scale — both exceeded the SKILL's "poll up to 10 min" ceiling and were still `status:running` (finished_at null, zombie_running:0, advisory locks held = alive, NOT wedged) when the trigger task ended. Do NOT read "still running past 10 min" as a wedge; the…

### cadence_write_path_shipped.md
*"CofC ruling + cadence_admin_api SHIPPED 7/8: snapshots/drift-guard as admin endpoints, NOT daemons; doctrine canonical"*
2026-07-08: resolved the [[p1-p2-agent-build-2026-07-06]] deferral. Council (PRO/CONTRA/HISTO + FATHER, all-B unanimous) ruling in `docs/DECISION_CADENCE_WRITE_PATH_2026_07_08.md` — **DOCTRINE (canonical): externally-triggered admin endpoints writing prod PG = application write path, NOT daemons under the factory read-only decree.** MUST NOT: in-app lifespan schedulers, factory→prod write_service …

### canary_tests_a_transport_the_mesh_never_uses.md
*"goose-canary drives goose DIRECTLY at a provider; the mesh drives it through the ladder shim on :8796 — so the version gate is blind to the shim, rung rotation, key hydrator and salvage. FU-119."*
Found 2026-07-27. The canary sets `OPENAI_BASE_URL=https://api.cerebras.ai/v1`. The mesh points goose at **`127.0.0.1:8796`** (ladder_shim). The gate deciding whether a goose version may touch the runtime exercises a request path the runtime never takes.  **Both halves bit in one run.** *False alarm:* on the direct path the 2nd turn of any tool-calling conversation against a REASONING model 400s —…

### canonical_family_materialized.md
*7/18-19 canonical_family SHIPPED to prod (PR*
**canonical_family LIVE in prod (2026-07-19 ~04:40Z), chairman-directed, CofC 3+FATHER-plotted.** PR #1621 merged (migration 0010: `canonical_family` VARCHAR(512) indexed + `canonical_rule` + `canonical_set_at` on mcp_server_registry; `tools/canonical/family_rules.py` frozen contract + 6 tests; `materialize_canonical_family.py`). Results: **232,174/232,174 rows filled (100%), families=162,832 — ex…

### capacity_rungs_openai_compat.md
*"Generic openai_compatible ladder adapter + NVIDIA & Cerebras capacity rungs; substring secret loader; current model ids and the probe gate."*
To escape MiniMax 429 capacity storms, the ladder got a **generic `openai_compatible` backend adapter** (`escalation.py` `_call_openai_compatible`, registered in `BACKEND_ADAPTERS`): `ModelSpec` carries `base_url`, `key_env`, and `extra_params` (provider tuning), so a new OpenAI-SDK provider is a config ROW, not new code. **Four** opt-in capacity rungs wired this way (NOT in the default builder pa…

### ci_gates_pr_head_tree_not_merged_tree.md
*"CI gates run on the PR HEAD tree, never the squash-merged tree that deploys — compare TREE shas not commit shas; fixed by tools/verify_deploy_candidate.py (PR #2043)"*
**"main is green" was never checked.** In zo-sentinel, `pr-gates.yml` (capmap-check, static-analysis, smoke-ladder, frontend), `no-hollow.yml` and `schema-prm.yml` are all `on: pull_request` **only**. GitHub evaluates them against the PR *head*. A squash merge onto a base the branch never saw produces a **third tree** — and that third tree is exactly what [[prod_drift_sentinel_cofc]] stages for th…

### ci_green_is_not_tower_green.md
*"A directory-walking assertion passed 18 CI checks and broke the deploy verifier 5 min later — __pycache__ exists on the tower, never in a fresh runner checkout. #2170 → #2171."*
PR #2170 merged **green on all 18 GitHub checks** and turned `dockerfile-copy-list` RED inside `tools/verify_deploy_candidate.py` on the tower five minutes later.  Cause: the new assertion walks every DIRECTORY under `services/active/` and demands its import_path resolve to a file. `__pycache__` is a directory under `services/active/`. It does not exist in a fresh GitHub runner checkout; it **does…

### ci_runs_on_a_different_platform_than_the_tower.md
*"fu_context --fu 103 --dry-run exited rc=1 on the tower for four days because FU-103's own title carries U+21C4 and Windows stdout is cp1252; Linux CI encodes UTF-8 and could never see it"*
`fu_context.py --fu 103 --dry-run` — the one command the PROTEAN CHARTER tells 13 lanes to run daily — exited **rc=1 `UnicodeEncodeError: 'charmap' codec can't encode character '⇄'`** on the tower. Every anchor resolved correctly; only the final `print()` threw. Fixed + merged 2026-07-30, PR #2408 squash `ea63332b` (`_harden_stdout()` + `_emit()`), suite 26→28.  **Why:** the ledger is authored wit…

### cleanup_not_verified_is_a_claim.md
*"A worktree teardown that reported success left 3,466 files; the fix then false-alarmed on a transient handle. Retry, THEN still fail. PRs #2066/#2067."*
2026-07-27: `prod-drift-sentinel` could not create its dry-run worktree — `git worktree add` died on `fatal: 'D:/zo/_prod_dryrun' already exists` while `git worktree list` showed **no such worktree**. The prior run's `git worktree remove --force` had pruned the metadata and left **3,466 files** on disk; neither `remove` nor `prune` can heal that state. That run's state file said `"worktrees_cleane…

### cleared_was_a_terminal_state_with_no_exit.md
*"2026-08-05 FU-257 — peer_review.sweep() scanned ACTED/REVERT_FAILED/PROPOSED and never CLEARED, so FU-231's cohort gate sat cleared-and-unexecuted for 19h through 9 sweeps by 7 lanes, all printing clean and returning rc=0."*
**2026-08-05 (FU-257).** `peer_review.sweep()` scanned exactly three states — **ACTED, REVERT_FAILED, PROPOSED** — and **never CLEARED**. A decision that passed peer review and was then never acted on was, to every surface this fleet owns, **byte-indistinguishable from one that had never been filed**.  **The cost.** `fu231-first-cohort-peer-clearable` — the **FU-231 first staged→active cohort gate…

### cofc_wire_lane_deferred.md
*"CofC 2026-07-20 ruling on the mount lane — what to do now, what not to do, and the exact reopen triggers"*
Council 3+FATHER, 2026-07-20, on the wire/mount lane (context: [[edit-class-directives-structurally-unbuildable]]).  **Banked unanimously:** gate attribution (#1669) and refill the queue (#1670).  **DO-NOW, still open:** emission-contract validator (landed only as PHASE 9 spec target `edit_class_directive_validator.py`, deliberately NOT wired into the generator); wire-lane recipe with `app_router_…

### commit_before_you_deliberately_break_something.md
*"Running a negative control on an UNCOMMITTED patch — `git checkout -- <file>` undid the deliberate break AND silently discarded the whole fix (2026-07-29)"*
Proving a new assertion can go RED ([[an_assertion_never_seen_red_is_not_evidence]]) means deliberately breaking the code, then restoring it. On 2026-07-29 the break was applied on top of an **uncommitted** patch, and the restore was `git checkout -- tools/ops_audit_state.py` — which reverts to the INDEX, i.e. `origin/main`. That wiped the deliberate break *and the entire fix* in one move.  **Why:…

### competitor_conduid_public_by_default.md
*"Conduid (conduid.com) is the direct competitor — 47.6K servers, ZERO auth on the read side; we lose on distribution, not data."*
Identified 2026-07-27 (chairman found it ranking on a "Qualys MCP" search). **conduid.com** — "The Trust Layer for AI Agents", 47.6K MCP servers scored 0–100, GitHub org `ill-ion/conduid`, `@ConduidHQ`.  **The architecture is the lesson, and it inverts our assumption.** We assumed Clerk forced auth-only and that SEO needed a clever gated-content carve-out. Conduid gates *nothing* on the read side:…

### copy_guard_read_dirname_not_import_path.md
*"The FU-102 COPY guard checked <dir-name>.py at the repo root, but the spine imports service.toml's import_path verbatim — all 51 staged services point into a tree the Dockerfile never COPYs. #2170/#2171."*
`tests/test_dockerfile_copy_covers_active_services.py` was written after prod v64 mounted 7 services that were never COPY'd into the image. It asked: *does `<service-dir-name>.py` exist at the repo root and is it in the COPY-list?*  **That is a proxy.** The spine never imports a directory name. `tools/generate_spine.py` copies `[service].import_path` verbatim into `SPINE_MOUNTS`; `app/_spine_gener…

### cost_ceiling_watched_the_quoted_dph.md
*"weekly_rescore's COST_CAP compared against the vast OFFER's dph (compute only), not the rented instance's dph_total (compute + storage) — 8.4% under-count. Fixed PR #2065."*
`ph_fire` stamped `state["dph"]` from the vast **offer's** `dph_total`. An offer prices compute only; once the instance is rented vast adds the allocated-storage component, so the live instance's `dph_total` is **strictly higher**. `ph_watch_collect` then compared `COST_CAP_USD` against that stale offer rate for the whole run.  Measured live on campaign wave `20260727-105859`: `state.json` dph **0…

### cve_axis_differentiator_threshold_autonomy.md
*"7/24 CVE is the sparsest/highest-value axis (613 vuln_links/298 servers vs 221,885 advisories); PHASE 14 #1796 strengthens it (family propagation +398, linker v2 version-range/dependency/alias); AUTOPOIESIS extended: the loop sets+tunes signal thresholds, no chairman quality-gate"*
7/24: with the moat 100% distinct-URL scored (278,026 servers), the 7 LLM axes are DENSE, so the real differentiator is the DETERMINISTIC `has_known_cve` axis — and it is the sparsest signal we hold. Prod read: `vuln_advisories`=**221,885** (OSV/CVE corpus) but `vuln_links`=**613** across only **298 servers** in **189 canonical_family** groups; the linker matches ONLY `package_exact` + `repo_exact…

### cve_surfacing_shipped.md
*"CVEs finally surface in the app (#1481, deployed+verified 7/15 00:1xZ). Root cause: backend/data/flags ALL healthy — the SPA never called /api/servers/{id}/vulns. UI-orphan class of bug: a shipped API nobody renders is as invisible as no API."*
**Shipped 2026-07-15 ~00:12Z: PR #1481 (merge 4020bba), fly deploy done, re-measured live.**  ## The "no CVEs surfacing" mystery — where the break actually was Checked in order, ALL healthy: - Kill-switches: `vuln.enabled=True`, `vuln.otx_enabled=True` **verified inside the Fly machine** (policy.flag, env `ZO_VULN_ENABLED` ← Fly secret; code default is False/fail-closed). - Data in prod PG: vuln_a…

### daemon_reload_protocol_fixed.md
*2026-07-02 PR*
2026-07-02 (late): **PR #1156 merged + deployed + live-verified** — the daemon-supervision fundamental fixed same-day (chairman: nothing left for tomorrow).  **Root cause of the recurring reload orphaning (bit us twice today):** graceful daemons (goose_runner, sentinel_directive_generator_goose) trap SIGTERM and exit **rc=0**; `daemon_wrapper.sh`'s contract treats rc=0 as "clean stop, do not respa…

### dead_check_must_not_gate_keystone.md
*"A provably-dead CI check must NOT block the keystone — I should have merged #1786; don't let a ghost gate spineful emission"*
7/25 chairman ruling: I HELD keystone PR #1786 (Autopoietic Loop v1 — service atomic unit + fail-loud spine) because `treewalk-smoke` = FAILURE, and queued it behind "rebase + CofC." That was WRONG. `treewalk-smoke` is a REMOVED/dead check ([[FU-084]]) — a ghost, not a real signal — and every REAL gate was green (pytest, db-integration, no-hollow, capmap-check, schema-prm, CodeQL). #1786 is the ke…

### declarative_policy_layer_shipped.md
*2026-07-02 PR*
2026-07-02 (evening): **PR #1148 merged + deployed** — the gate/sentinel PATTERN is retired, replaced by one declarative policy layer.  **How it works:** `zo_sentinel/policy.py` resolves every operational knob through one precedence chain, fresh per read (live flips, no restarts): embedded defaults < **`zo_sentinel/policy_defaults.toml`** (TRACKED = reviewed posture-as-code; currently declares all…

### dedup_pass_and_delegation_2026_07_03.md
*"First prod-era pipeline-watch ALERT handled (16 dup/superseded PRs closed, gates already ON); chairman delegates decisions to CofC 3+FATHER"*
2026-07-03: pipeline-watch check-E ALERT (open auto/build 48 > 25, trajectory 9→20→19→25→48) handled: closed 16 PRs — 8 exact-title dup re-emits (keep-newest: kept #1136/#1153/#1142), 2 stale near-name variants (#1092/#1104), 6 SUPERSEDED (target file already on main: #1163/#1162/#1124/#1090/#1074/#1071 — regression risk if merged over newer code). 48→36 remaining, all unique/unsuperseded. **queue…

### deploy_heartbeat_gateway_fallback.md
*"2026-07-02 deploy-from-main heartbeat — Zo connector absent, gateway has no run_script; tower fallback client + refresh_code collision root cause"*
2026-07-02 run of the `deploy-runtime-from-main` scheduled task. Two durable findings:  **1. The scheduled task's assumed tools don't exist in this environment.** It calls `mcp__d4605fd3...` `zo_run_script name="refresh_code"` / `zo_read_file`. In the Cowork/scheduled session the Zo MCP connector is NOT loaded (no `mcp__d4605fd3*` tools; ToolSearch finds none). The Zo **gateway** (`https://api.zo.…

### deployment_hosting_and_sft_shim.md
*"MVP hosting decision (Fly.io #1, GCP=scale-not-MVP), the weekly deploy-compat rehearsal test, the SFT->app schema shim + reconciliation findings, and the existing tower-local FE/API assets to reuse. 2026-06-24."*
**Hosting (research only, NO infra committed per chairman 2026-06-24).** Subagent ranked MVP hosts for the FastAPI+Postgres container (factory STAYS on tower; only the app deploys): - **#1 Fly.io ~$8-12/mo** — Dockerfile deploys as-is, `release_command="alembic upgrade head"` in fly.toml = the Procfile release phase, static IPv4 + WireGuard mesh makes the low-volume `mcp_llm_axis_scores` push from…

### directive_generator_architecture.md
*"sentinel_directive_generator.py v1.2 — generation flow, three validator gates, quality-gate circuit breaker, standing-goals fallback. How directives reach the builder."*
`sentinel_directive_generator.py` v1.2 (path `/home/workspace/zo_sentinel/`). Runs as a supervised daemon, polls every N seconds, generates directives only when `queue_depth < MIN_QUEUE_TO_SKIP`.  **Cycle flow:** 1. Refresh DB schema doc (subprocess to `refresh_schema_doc.py`) 2. Build prompt from: schema + failed modules + recent build failures + registry summary + Layer-1 knowledge (`assemble_la…

### directive_generator_stuck_2026_05_25.md
*"As of 2026-05-25 evening, directive_generator has been writing 0 directives per cycle for 3+ hours. Root cause is the quality-gate circuit breaker quarantining every file MiniMax proposes."*
**State on 2026-05-25 ~20:13 UTC (4:13 PM EST onward into evening):**  Every directive_generator cycle for the last 3+ hours produces the same outcome: - `Queue depth: 0` - LLM (MiniMax) suggests 7-8 directives - ALL get rejected: `quality gate blocks rebuild of <X>.py: circuit breaker tripped -- manual reset required` - `Generation complete: 0/N directives written` - Standing-goals fallback: `emi…

### directive_quality_does_not_bypass_tier0.md
*"Controlled n=2 test 2026-07-29: directives carrying explicit negative schema constraints STILL emitted invented model imports and STILL merged Tier-0-degraded. Directive quality cannot be measured until FU-031's Tier-0 rate nears 0."*
**2026-07-29, 02:01–02:03Z.** Chairman hypothesis (7/28): post-ratchet + spineful emission + Graphify↔ledger binding should have raised the rate of *useful* builder syntax, so a well-formed directive ought to build clean. Tested it properly instead of arguing.  Two directives emitted into the LIVE queue, each carrying all three `builder_lane.md` preconditions **plus** explicit negative constraints…

### discovery_funnel_unwedged_registry_sync.md
*"7/16 — discovery funnel was writing into a VOID (mcp_discovery_candidates never existed); 3 fixes + new tower registry_sync closed local→prod gap (+2,466 day one)"*
2026-07-16: root cause of flat PLAN_200K counts = the ENTIRE discovery funnel dead-ended. Three stacked wedges, all fixed:  1. **`mcp_discovery_candidates` never existed** in mesh DuckDB. Every ingestor's `CREATE TABLE IF NOT EXISTS` silently failed because :8772 `/query` force-appends `LIMIT` → DDL parser error. DDL must go via **`/execute`** (exists, used by promoters' UPDATE). Created the table…

### drift_check_cost_ceiling_232k.md
*"REOPENED 7/19: cap fixed (#1596) but Fly-side reindex is OOM-bound at 232K — runs 24+26 both died as zombie 'running' rows; OOM kills co-resident cadence jobs too. Fix = tower-side or batched reindex"*
2026-07-17 cadence run: `perspective_snapshots` ok (run 20, 5 perspectives, 67 events queued, ~4 min). `ask_corpus_drift` (run 21) FAILED in 2s: `cost ceiling: registry_rows=232115 outside (0, 200000]`.  **Why:** the [[plan_200k_committed]] sprint ([[discovery_funnel_unwedged_registry_sync]]) grew the registry past the drift-guard's hard cap. The ceiling did its job (halt, don't burn budget) but i…

### duplicate_daemons.md
*"As of 2026-05-25, three sentinel daemons are running twice — directive_generator (PIDs 6238+8802), gate_scheduler (6351+9176), liveness_probe (6411+9567). Symptom: every log line printed twice."*
`zo_agent_health` shows duplicate PIDs for:  - `sentinel_directive_generator.py` — PID 6238 AND PID 8802 - `gate_scheduler.py` — PID 6351 AND PID 9176 - `liveness_probe.py` — PID 6411 AND PID 9567 (in zo_mesh)  Symptom in logs (`goose_runner.log`, `sentinel_sentinel_directive_generator.log`, etc.): **every single line printed exactly twice with identical timestamp**. Confirms two processes share t…

### e2e_cutover_baseline.md
*"Where the app functional-E2E snapshot is stored (GH Actions artifact app-e2e-snapshot-<backend> on e2e-nightly) and how to use it to verify the DuckDB->Postgres app migration preserves behaviour. Harness: tests/integration/test_app_e2e.py (PR #523)."*
**Purpose:** prove the planned app-data **DuckDB -> PostgreSQL** migration (`docs/POSTGRES_APP_MIGRATION_SCOPE.md`) preserves app behaviour, at the APP level (complement to the scope doc's row-count/checksum verify). Built PR #523, 2026-06-24.  **Harness:** `tests/integration/test_app_e2e.py`. Talks the **write_service HTTP seam** (`/execute`, `/write`, `/query`) — the exact DB-access boundary the…

### edit_class_directives_structurally_unbuildable.md
*"WHY 246 routers stay unmounted — wire/integrate directives declare output_file null, which no-ops 4 of 6 gates and both recipes forbid the mount"*
The mount lane is not neglected, it is **impossible**. Root cause, verified live 2026-07-20:  The architect emits `wire_*` / `integrate_*` directives with `handler: "generate_file"` and **`output_file: null`**. Two things chain off that one field:  1. In goose_runner's gate chain, `declared_output(directive) is None` → `output_confirmed` trusts the build and the syntax / schema-PRM / no-hollow / *…

### editing_a_crlf_ledger_in_text_mode_strips_every_cr.md
*"FOLLOWUPS.md's line terminator FLIPS between runs (CRLF again at 2026-08-07T11:43Z after LF-only at 09:4xZ); never assume a convention AND never freeze a count -- assert CR delta == LF delta, which is the only form correct for both an append and an in-place edit"*
2026-08-06 zo-sentinel-pipeline-watch. Appending one dated bullet to `D:\zo\Zocomputer Agents\FOLLOWUPS.md` (1.87 MB, uniformly CRLF, verified LF=CRLF=4177 bareLF=0) via `open(p).read()` → splitlines → `open(p,"w").write()` **stripped the `\r` from every one of ~4200 lines** — Python universal-newlines translates `\r\n`→`\n` on text read and writes bare `\n` on Linux. The intended +1 bullet showed…

### engine_fallback_and_anchor_refill_shipped.md
*2026-07-02 the two fundamentals shipped — PR*
2026-07-02 (afternoon): chairman-directed deep fixes landed — **PR #1133 (engine fallback) + PR #1134 (self-refilling anchor), both merged + deployed + gated ON.**  **#1133 — first-class deterministic engine** (`zo_sentinel/engine_build.py` + minimal goose_runner hook). Root insight: the fallback engine already ran per-attempt but was STARVED — goose got graph+lessons+data-access grounding, the en…

### every_cure_existed_and_none_was_reachable_from_a_shell_prompt.md
*"inline-interpreter-source had a cure for all 10 bites and every cure was an importable Python function, while every bite happened at a PowerShell prompt where no interpreter was running yet"*
FU-306, improvement-loop cycle-0033, 2026-08-10. `inline-interpreter-source` was RED at 10 bites / 3 lanes. Classifying by MECHANISM instead of by the family label (see [[a_hazard_family_label_hid_that_three_of_four_bites_were_one_call_site]]) split it four ways — 4 shell-parse of the `-c` payload, 3 native-stdout-PIPED-into-`python -c`, 2 child-encoder cp1252 on U+2192, 1 misfiled — **and `fricti…

### every_knowledge_surface_is_pull_and_enumerated_from_the_wrong_end.md
*"2026-08-04 — ledger/graphify/memory all answer 'tell me about X'; none answers 'what exists that nothing points at?'. 15 of 88 tools dark, 8 mentioned nowhere"*
**`tools/lane_halt.py --enforce` was built, shipped and ARMED on 2026-07-30 and had zero callers five days later** — as did its only producer, `queue_census.py`. Its docstring carried a section headed *WHAT ARMING DOES NOT DO (read this before trusting it)* and was exactly right. **The honesty was perfect and changed nothing: a caveat in a docstring has no subscriber.**  **All three knowledge surf…

### every_lane_complied_and_the_sweep_threw_the_evidence_away.md
*"peer_review --sweep wrote NOTHING while zero decisions were ACTED, so FU-254's own \"sweep line from 3 lanes\" predicate was unsatisfiable by construction; fixed 2026-08-05 with an unconditional top-level `sweeps` map"*
**2026-08-05, vast-jobs-daily-audit.** `peer_review.py --sweep` left **zero trace in `peer_decisions.json`** across two days in which `lane_start.py:141` invoked it on every single lane start. Measured: the string `sweep` occurred **0 times** in the pre-change store (11165 B).  **Why:** `sweep()`'s only write was the per-decision `sweep by <lane>: verify GREEN` line, emitted exclusively inside `fo…

### exec_module_without_sys_modules_mimics_the_diverged_copy_hazard.md
*"importlib spec.loader.exec_module on a dataclass-bearing module fails identically on BOTH copies unless sys.modules[name]=m is set first — looks exactly like the fu_ledger two-diverged-copies hazard but is the importer's fault"*
2026-08-31, prod-drift-sentinel run: importing `fu_ledger.py` by file path via `importlib.util.spec_from_file_location` + `exec_module` raised `AttributeError: 'NoneType' object has no attribute '__dict__'` inside `dataclasses._is_type` — on the shared checkout AND the lane-tree copy. First diagnosis was [[the_tools_fu_ledger_copy_is_a_diverged_shadow_not_a_stale_one]] (diverged copy); wrong, and …

### exemplar_doctrine_adopted.md
*"2026-06-28 adopted Gemini's \"Exemplar Doctrine\" architecture playbook for the autonomous builder, with the key correction that it must be ENFORCED in code, not prose"*
2026-06-28: adopted Gemini's **Zo Sentinel Architecture Playbook** (uploaded PDF) as the strategic frame for the autonomous builder. Core = the **Exemplar Doctrine**: small builder models hit a capability floor + context-collapse under heavy context (Qwen2.5-Coder finding) → they emit hollow stubs; fix = hard-mount a real working file via the `module_from_exemplar` recipe and let the small model a…

### failure_playbook_and_plus0_trap.md
*"The ~9 recurring failure classes + their FALSE-POSITIVE guards live in failure_classifier.py PLAYBOOK (#358). Critical: generator +0 / 'no_novel_builds' is usually a MODEL-PATH failure, NOT a generation bug — check the shim FIRST."*
The zo-sentinel failures are rarely new — the same ~9 classes recur and the *diagnosis* kept being re-derived from scratch. They're now encoded in `failure_classifier.py` (`classify_line`, `CLASSES`) + a `PLAYBOOK` dict (`{root, false_positive, fix, refs}`) seeded from **`DESIGN_graph_native_feedback.md`'s "Regression caveats — MUST hold"** (the pre-existing June-2026 playbook — the graphify→archi…

### false_positive_and_hollow_scaffolds.md
*"Two findings 2026-06-25: (1) the overall_risk model FALSE-POSITIVES official big-tech MCPs as HIGH (conflates surface with trust, ignores its own maintainer_trust=ESTABLISHED) -> defamation risk; fix = trust_gating_override. (2) the auto-built app modules are the RIGHT modules but HOLLOW (don't read mcp_llm_axis_scores); fix = recipe-selector hint broadening."*
**FINDING 1 — false positives on official big-tech MCPs (defamation + accuracy).** Of 2,791 scored, overall_risk = MEDIUM 1869 / **HIGH 671 / CRITICAL 98** (~28% HIGH+). Flagged HIGH: official **Microsoft (azure, fabric), Stripe, Supabase, Cloudflare, ~10 Google Cloud** endpoints (run/sqladmin/compute/container/bigtable/...). ROOT CAUSE (per-axis breakdown): for Stripe/Azure/Google-Run the model C…

### false_stale_heartbeat_is_an_architect_input.md
*"write_service read \"stale 4h8m\" in the architect's wiring map while provably serving; the false signal is what built the diagnose_* graveyard."*
2026-07-28: the architect's layer-1 wiring map labelled `write_service` **stale (4h8m)** at 09:09:18Z while it was **serving** — `POST :8772/query {"sql":"SELECT 1 AS ok"}` → `{"rows":[{"ok":1}],"count":1}`, process unbroken since 05:00:57Z, builder writing through it at 08:57Z. Same shape on `mcp_scanner`, `anti_entropy`, `wisdom_synthesiser`: all beat once at ~05:02Z after start, then never agai…

### fence_fix_494_validation.md
*PR*
PR #494 ("builder: robust _strip_code_fences") merged 18:03 UTC 2026-06-23, runtime commit 75af896, goose_runner relaunched pid 26758 at 18:08 UTC.  **Result: fence ghosting is CURED.** After the 18:08 restart, multiple directives cleared Tier-0 and emitted build_artifact — several via the *ladder-shim fallback path itself* (exactly what #494 fixed): permission_scope_diversity_enrichment (10620B),…

### fifty_of_the_broken_services_are_directories_with_no_source.md
*"The spine hygiene red is ~35% manufactured by empty directories, and the SKILL's stated recovery (\"publish them, never delete\") is structurally impossible for those."*
Measured 2026-08-12 on `/home/workspace/zo_sentinel` by `deploy-runtime-from-main`. **The hygiene spine number has no pinned basis** — three defensible bases give different answers on the same tree:  | basis | value | |---|---| | `ls -1d services/active/*/` (filesystem) | 170 | | `git ls-files services/active` distinct dirs (tracked) | 33 | | filesystem − tracked | **137** | | `git ls-files --othe…

### fire_gate_replaces_staged_only_proxy.md
*"prod's image surface is an EXPLICIT Dockerfile COPY list; services/, tools/, .github/, tests/ enter no image"*
`tools/fire_gate.py` (PR #2183, merged 2026-07-28T17:02Z, squash `0ada3c0c`) answers "would the image built from today's main differ from the verified one?" — `--staged <sha>`, exit **0 SAFE / 1 RESTAGE / 2 ERROR**. rc=2 is never readable as SAFE ([[probe_that_cannot_evaluate_is_not_a_red]]).  It **replaces** the prose rule in `prod_deploy_staged.md`: *"safe provided compare/<staged>...main is ser…

### first_real_scores_since_0624_blocked_by_fu094.md
*"RESOLVED 2026-07-26 (PR #1953, FU-108). Run 20260726-014732's 20,576 REAL scores LANDED — gate had extracted 0 rows from 20,576 valid records and condemned data it never read. Three defects fixed: shared extractor, contract from SFT schema, per-axis thresholds. Round-trip verified."*
**RESOLVED 2026-07-26 — the wave LANDED.** PR **#1953** (squashed to `f28ceb2`), all 16 CI gates green. Import: **20,576 servers / 144,032 rows, coverage 100.0%**, `scored_servers` 278,026 → **278,602**, `newest_scored_at` 07-24 → **2026-07-26T23:14:21**. Round-trip verified: the distribution read back OUT of the moat matches the preds file **exactly on all 7 axes**. Rollback was a restore-verifie…

### first_staged_fire_converted_v65_accepted.md
*"2026-07-29 — chairman fired the 19-times-staged SHA; accept_gate returned ACCEPT rc=0 live, all 7 dead services up, prod now attests its own git_sha"*
**2026-07-29T17:26:10Z: the prod stage finally converted.** Chairman fired `7fc39201d8aea5f50017bf893843694e5a77f7f1` → Fly **v65**, image `registry.fly.io/mcplookup:deployment-01KYQEJQJH4Q541KQSN25A3X3J`.  `prod-drift-sentinel` verified it at 19:51:13Z with `python tools/accept_gate.py --sha … --once --json`: **ACCEPT, rc=0.** `/health` 200; `/spine/health` 200 `ok:true`; **31 services mounted, 0…

### fix_landed_in_the_watcher_not_the_actor.md
*"#2066/#2067 hardened verify_candidate.ps1; deploy_prod.ps1 — the only path that writes prod — kept the identical silent-orphan teardown all day. Fixed in #2068."*
2026-07-27. The morning runs repaired the sentinel's **staging** script (`ops/host/verify_candidate.ps1`) after `git worktree remove --force` pruned the metadata and left 3,466 files on disk — see [[cleanup_not_verified_is_a_claim]]. The identical un-hardened teardown sat untouched in `ops/host/deploy_prod.ps1`, the **only** path that actually writes prod:  ```powershell if (Test-Path $WorktreePat…

### flyctl_login_timer_is_a_fleet_wide_time_bomb.md
*"2026-07-28 — flyctl's client-side 720h re-login timer aged out at 730h29m and blocked 10 scheduled tasks + 5 repo tools. The TOKEN was valid throughout. FU-134/FU-137/FU-149. INVERTED 07-29 at 765h: the fleet moved to an AgentVault machine token, so a bare ambient `flyctl auth whoami` is now permanently rc=1 BY DESIGN — a guaranteed false positive, not an outage. Hydrate via _tools/fly_auth.py before judging."*
`flyctl` enforces a **client-side 720h (30-day) re-login timer** keyed on `last_login` in `~/.fly/config.yml`. On 2026-07-28 it aged out at **730h29m** — about 10.5h before the weekly rescore fired, which is exactly why the 07-27 11:01Z wave worked and the 07-28 06:13Z one did not.  **The token was never the problem.** Proven, and worth re-proving before you believe any "expired token" story here:…

### flyctl_ssh_returns_rc1_for_every_command_when_unattended.md
*"flyctl ssh console -C exits 1 on SUCCESS when no console is attached, collapsing a three-state exit contract into one value; the remote shell also strips one layer of quoting"*
**2026-08-06, clerk-signup-reconcile-nightly.** Run detached (no console attached), `flyctl ssh console -a mcplookup --machine <id> -C <cmd>` returns **rc=1 for EVERY command**, stderr `Error: The handle is invalid.` — including `--self-test` printing `self-test: 14/14` and the reconcile printing its GREEN line. **flyctl's rc is not the remote rc.**  Why it matters more than a flyctl quirk: the cl…

### foreground_launch_manufactures_the_orphan.md
*"#2173 taught verify_candidate to HEAL an orphan worktree; the foreground MCP launch creates a fresh one every slow run. FU-138."*
`verify_candidate.ps1` takes 90s+ (smoke-ladder alone is 66.6s) and `prod-drift-sentinel` ran it in the **foreground** from the agent/MCP shell. The request times out, the parent PowerShell is torn down, and the child dies **after** writing its verdict JSON but **before** `git worktree remove --force`. Observed 2026-07-28T10:57Z: verdict written at 10:57:29Z, PID gone, **1,621 files stranded** in …

### frontend_lane_fixed_dispute_ui_shipped.md
*2026-06-29 fixed the agentic webapp recipe lane (BOM + schema-PRM) and the ladder autonomously built+merged the dispute admin UI (PR #1042)*
2026-06-29: The "frontend won't ship via the ladder" recurring failure is root-caused and FIXED, proven end-to-end by injecting a directive and watching it build + merge.  **Two real bugs (the earlier "py_compile rejects .html" was a misread — _syntax_gate has guarded suffix!=.py since #96):** 1. All THREE webapp recipes (`webapp_frontend_react`, `webapp_backend_fastapi`, `webapp_fullstack`) ha…

### fu031_dominant_cause_is_syspath_not_naming.md
*"FU-031's 103-of-104 tier0 bucket is a sys.path[0] artifact from moving builder output into services/staged subdirs — NOT the 07-20 model-naming family. One-line PYTHONPATH fix, verified."*
On 2026-07-27 the FU-031 probe bucketed 104 tier0-degradations as **x103 `ModuleNotFoundError: No module named 'app.db'`**. `app/db.py` exists and imports fine from the repo root — so this is a HARNESS bug, not a builder defect.  `goose_runner._selftest_gate` runs `subprocess.run([sys.executable, str(out)], cwd=PROJECT_DIR, env=_env)`. **Python sets `sys.path[0]` to the SCRIPT's directory; `cwd=` …

### fu031_fix_restores_signal_not_pass_rate.md
*"The FU-031 one-line PYTHONPATH fix does NOT \"reverse the sign of the P3 bar\" — probe says 4.7% PASS / 48% would-BLOCK. Degradation is a BUILDER-output property, not a harness one. #2177."*
2026-07-28. FU-031's ledger claimed the one-line `PYTHONPATH` prepend in `goose_runner._selftest_gate` was "deterministic and reverses the sign of the whole P3 bar". **Falsified before merging, by measuring instead of arguing.**  Probe (read-only, $0, ~15 min): ran all **381** staged self-test-bearing modules under the exact harness env *with* the fix applied.  | outcome | n | share | |---|---|---…

### fu102_copy_block_no_pr_in_flight.md
*FU-102 prod COPY-list block persists across commits because no fix PR is in flight; prod-drift-sentinel holds email on dedup*
7/26 01:54Z (prod-drift-sentinel): prod v64 vs main is 46 PRs stale, but a redeploy is STAGE-BLOCKED and will stay blocked — the 7 services/active modules (entity_report_exporter, org_api_key_manager, org_entity_search_api, overview_dashboard_api, server_axis_scores_summary_router, threat_intel_summary_api, verdict_watchlist_service) are still absent from the Dockerfile COPY-list and **no open PR …

### fu102_fixed_sentinel_acted_not_escalated.md
*"FU-102 (7 Dockerfile COPY gaps) closed by PR #2022 after 3 sentinel runs re-diagnosed it and waited; the waiting was the failure, not the gap."*
**2026-07-27 02:00Z — FU-102 RESOLVED by PR #2022** (squash `b22cb413`). One Dockerfile COPY line for the 7 `services/active` modules missing from the prod image since v64, plus `tests/test_dockerfile_copy_covers_active_services.py` as a permanent guard.  **The real lesson is not the fix — it is the 24.5h wait.** Three consecutive `prod-drift-sentinel` runs (01:20Z, 04:50Z, 19:50Z on 7/26) each re…

### fu104_run_20260726_REAL_scores_but_import_pending.md
*"7/26 run 20260726-014732 produced the first NON-DEGENERATE (real) scores since 6/24 — but the import phase never ran, so prod moat is still garbage."*
7/26: FU-104 canary verdict on rescore run `20260726-014732` (Vast instance 45871457, ~$0.27, RTX 4090) = **REAL SCORES**. First credible non-garbage output since 2026-06-24. Two independent proofs from collected forensics (`D:\zo\runs\weekly_rescore\20260726-014732\results\`):  1. **Adapter attached** — onstart.log: `[score-onstart] adapter OK: 29528024B + heads 267086B`; adapter_model.safetensor…

### fu104_run_stranded_post_export_pre_fire.md
*"Run 20260726-014732 stopped post-export/pre-fire. NOT a mystery launcher death: a DETERMINISTIC SystemExit from FU-093's post-push remote verify, which parsed `git ls-tree -r -l` with four-field indices and could NEVER return ok. Fixed FU-105 #1881."*
**CORRECTED ROOT CAUSE — supersedes the earlier "launcher death / went dark" read in this same file.**  Run `20260726-014732` (delta, 20,576 servers = 576 new + 20,000 refresh) exported, bundled, pushed, then exited before firing. From the outside it looked exactly like a silent pre-fire launcher death: no fire event, 0 live vast instances, no process, `$0`. **It was not.** `_fu104_canary.err` hel…

### fu_235_closed_the_migration_applied_without_moving_ownership.md
*"v69 proved option (D) — alembic ran as owner via OWNER_DATABASE_URL, 0011 applied, and pg_get_userbyid(relowner) on users is STILL mcplookup"*
**RESOLVED 2026-08-04T00:25:30Z, v69, sha `4a1d508a`.** accept_gate ACCEPT rc=0.  The discriminating measurement — taken read-only *inside* the running machine, not inferred from the deploy's exit code:  | | before (v68) | after (v69) | |---|---|---| | `alembic_version` | `0010_canonical_family` | **`0011_clerk_identity`** | | clerk cols on `users` | NONE | `clerk_id`, `clerk_created_at`, `clerk_s…

### fu_context_kl_is_a_trailing_indicator.md
*"FU-103's ledger⇄MEM⇄KL graph works end-to-end, but the graphify KL is rebuilt DAILY (09:50Z) — absence of a symbol in the KL is NOT evidence it didn't land. Verified 7/26: my 04:08Z merge was invisible to a 21:34Z-built KL."*
**The three-store loop works** (FU-103 P1+P2, confirmed end-to-end 2026-07-26): - `D:\zo\Zocomputer Agents\FOLLOWUPS.md` = single source of truth. Emitters APPEND `### FU-NNN` (next free number); only `follow-up-triage` changes `status:` lines. - `python explode_followups_to_memory.py` (run from that folder) regenerates one MEM node per FU → `.claude\projects\C--windows-system32\memory\followups\z…

### generator_plus0_not_breaker_333.md
*Generator +0 is NOT the breaker — PR*
As of 2026-06-20, generator `+0 novel` per cycle is NOT caused by the stale-tripped breaker. PR #333 (`chore/disable-quality-gate-binary-latch`) merged ~03:28Z disabled the binary quality-gate latch, yet the generator stayed +0 for 11.5h after — proving the breaker (state still "tripped", cosmetic) is not the cause.  **Real funnel head:** the `directive_architect` (MiniMax-via-shim) emits no `prop…

### git_status_cannot_see_an_empty_directory.md
*"A `git status --porcelain` census of untracked junk is structurally blind to empty dirs — the exact members that fake the T2 active-service count."*
On 2026-08-02 the deploy lane censused the `services/active/` squatter population two ways and they disagreed by exactly the members that matter. Of the **25** names `generate_spine.py --check --strict` flagged, **all 25** had `git ls-files` = 0, but only **22** appeared in `git status --porcelain | grep '^??'`. The 3 invisible ones were empty directories — **git does not report an empty directory…

### glama_fabricated_tool_count.md
*"Glama ingestor stamped tool_count:0 on ALL 48,544 rows (empty tools[] from API) — fabricated-zero fix PR"*
2026-07-04: chairman asked to "fix the glama issue" after sap-mcp-server (HUGO-Domon/sap-mcp-server, ~13 real tools) showed tool_count:0.  **Root cause**: Glama's list AND detail API return an EMPTY tools[] for every server it can't introspect (BYO-backend / unpublished / private-prep — verified live on glama.ai/api/mcp/v1/servers/<id>). discovery_glama_paginator.normalize_entry did `len(tools or …

### goose_143_prose_salvage_shadow_watch.md
*7/19 PM — TOOL:-as-prose salvage PR*
**7/19 PM session — goose modernization pass.**  **Phase-8 "mistral TOOL:-as-prose" ROOT-CAUSED + FIXED (PR #1635, merge pending):** live 7/14–15 logs show THREE prose shapes across nvidia/groq/cerebras rungs (never mistral-only): bare `TOOL: name` (dropped → +0), `TOOL: name` + fenced json args, and fabricated `TOOL: {json}` RESULT blocks (model roleplays the bridge; ×12 wedge on 7/15). New `_par…

### goose_extension_cwd_gotcha.md
*Goose stdio extension paths resolve relative to the Goose process cwd, NOT the recipe file location. Use ABSOLUTE paths in recipe `extensions.args[]` to avoid cwd coupling. Bit us 2026-05-26 in Phase 0b smoke.*
When a Goose recipe declares an `extensions:` block like:  ```yaml extensions:   - name: zo_directive_bridge     type: stdio     cmd: "python3"     args:       - "zo_sentinel/mcp_servers/directive_mcp.py"   # ← relative, cwd-dependent ```  The args path resolves **relative to whatever cwd the Goose process is launched from**, not the directory of the recipe file. In Phase 0b smoke, the smoke scrip…

### goose_runner_builder_ladder.md
*"goose_runner.py routes directives to Goose CLI tier1 → ladder_shim:8796 → escalation.py 16-rung. `is_goose_eligible()` only filters by .done.json presence — \"Skipping non-eligible\" means ALREADY BUILT, not stuck."*
Goose-tier1 builder went live 2026-05-25; proved healthy with two clean builds at 23:20-23:21 UTC.  **Path:** `/home/workspace/zo_sentinel/goose_runner.py`. Supervisord daemon. Polls mesh_events DB + `directives/pending/` every `POLL_SECS` (~60s).  **Routing:** - Each directive → `is_goose_eligible()` → returns False **only if** `/home/workspace/zo_sentinel/directives/<id>.done.json` exists - High…

### goose_swallows_dead_stdio_extension.md
*"goose does NOT abort when a stdio extension fails to start — it warns and runs the session anyway, so a broken bridge yields a heartbeat-healthy architect at +0. FU-117."*
Proven on the wire 2026-07-27 (goose-canary runs 30262516540, 30262672181): when `zo_directive_bridge` dies before initialisation, goose prints `Warning: Failed to start extension '<name>' (...), continuing without it` and **starts the session anyway** with only `bash`/`python`/`search`. The agent then emitted a perfectly-formed `zo_directive_bridge__read_protected_files` call into the void and fi…

### goose_version_gates_and_plus0.md
*"ZoCompute goose is 1.34.1 — too old for gemini_oauth/ACP, PreToolUse hooks, AND the /goal command (all 1.36/1.37/2.0). Upgrading goose is the linchpin. Also: the architect went +0 after the #390 recipe — MiniMax can't drive it; model is the bottleneck."*
**ZoCompute goose = Rust `block/goose` binary at `/usr/local/bin/goose`, version 1.34.1 (confirmed 2026-06-22).** NOT the Python `goose-ai` pip pkg — that's installed but a stub (`goose-ai 0.1.0`, red-herring); `KNOWLEDGE_BASE.md`'s "pip install goose-ai" is STALE. So upgrades use the Rust CLI installer (`download_cli.sh` w/ `GOOSE_VERSION`), per the goose-canary CI (`.github/workflows/goose-canar…

### goosetown_mapping.md
*"goosetown (goose-native parallel multi-agent orchestration) maps onto zo-sentinel's pipeline — and the highest-value borrows (research→propose orchestration + parallel worktree build delegates) hit BOTH our problems. Gated on the goose 1.38 upgrade."*
**UPDATE 2026-06-23 (verified vs live repo):** canonical repo is now **`github.com/block/goosetown`** (official Block project; aaif-goose is a mirror/fork). Requirement = **goose v1.25.0+** → the version gate I'd assumed (1.36/1.37) is **WRONG/CLEARED**: architect=1.38, builder=1.34.1 both already qualify (goosetown ships its OWN orchestration — `./goose` wrapper + gtwall + telepathy + bd — so it …

### gpu_scoring_run_and_trust_override.md
*"2026-06-25: scored the whole registry (65,532) via a one-shot ~$0.75 Vast 4090 pass; built+validated+PR'd the trust_gating_override (false-positive cap for official MCP publishers, PR #672). Also: the reusable GPU-scoring pipeline, the postgres direct-read path, and the it_write_service /query contention finding."*
**GPU SCORING (the way to score the registry, NOT CPU).** CPU resident scorer = ~13.7s/server on the tower's 4-core Xeon W-2223 -> ~10 days for 62K. Instead fired a **Vast RTX 4090 @ $0.335/hr** one-shot pass: scored **62,447** servers in ~2h14m for **~$0.75**. Reusable pipeline (tower-local, in `D:\zo\runs\v3.0_40974559_FULL\`): - `score_service_resident.py` = resident-model variant of score_serv…

### graphify_architect_consumption.md
*"The directive architect now CONSUMES the graphify graph two native ways (goose MCP read-tools #390 + goose Memory seed #389) instead of a flat already_built name-list — the fix for the ~5-subject fixation / graphify ROI gap."*
graphify built a rich call/import/inherit graph (~50 Leiden communities = the app's domains, `code_nodes`/`code_edges`) but the DIRECTIVE architect only ever consumed it as a FLAT `read_already_built()` name list → it fixated on ~5 subjects (snow/aidr/etc). Robin's framing: "a whole knowledge layer and nothing consumes it." Fixed the goose-NATIVE way (don't reinvent goose's context layer):  - **#3…

### graphify_fu_anchor_sync_phaseB.md
*graphify-kl-daily-refresh now has a Phase B that reconciles open-FU code-anchors against the fresh graph + caches per-FU subgraphs*
2026-07-26 (chairman-requested): the `graphify-kl-daily-refresh` scheduled task gained **Phase B — FU↔graph anchor-sync**, the ledger↔KL half of [[fu_context_kl_is_a_trailing_indicator]] / FU-103. After the KL refresh it reconciles every open FU's `.py` code-anchors against `graphify-out/graph.json`: an anchor whose basename is absent = ledger drift (unbuilt / renamed / lives outside the graphifie…

### graphify_schema_prm.md
*2026-06-28 GraphifyKL finished — schema knowledge layer (schema_kl.py) + deterministic pre-build PRM gate + capable-rung fail-over. PR*
2026-06-28: Built the durable grounding layer the chairman wanted ("finish the graphify work"). PR **#1006** merged to main, deployed to box (HEAD 3b0681c), goose_runner reloaded (pid 54219). Verified live: 2nd green ladder build (axis_distribution_api.py) passed the new gate.  **What shipped (builder-internal + CI only; no app/Fly runtime change):** - `schema_kl.py` — introspects the REAL `app.mo…

### hollow_scaffold_root_cause_recipe_schema.md
*"2026-06-26 ROOT CAUSE of the ~142 hollow autonomous-build PRs found + fixed: the builder recipe webapp_backend_fastapi.yaml had ZERO DB schema knowledge and explicitly told the model to stub the session + self-test on in-memory SQLite. Fix = inject the real app.db/app.models imports + the real mcp_llm_axis_scores/mcp_server_registry schema into the recipe (PR #791). Exemplar real endpoint = verdict_breakdown_api (PR #792)."*
**CHAIRMAN INSIGHT (correct): graphify KL / DB schema was NOT reflected into the build recipes, so the builder invented fake schemas.** Confirmed: of the open autonomous-build PRs, ~142 (#357-788) were ALL hollow scaffolds -- inline placeholder/mock DB (in-memory dict named `mcp_llm_axis_scores_db = {}`, in-memory SQLite, a local `get_session` stub, or real imports commented out), `fastapi.testcli…

### host_topology_verified.md
*"VERIFIED 2026-07-28 - no PG on the tower (the old note was FALSE), 8772 is runtime-loopback by design, the bridge is EXECUTION via zo_call.py; read BRIDGES.md before assuming any route"*
Each fact below was checked on 2026-07-28, not recalled. Re-check before relying on it; that is the point of this file.  * **Tower has NO PostgreSQL.** No service, no process, no install dir, no   `psql` on PATH. The prior memory "Tower Postgres standing - PG16.6 :5432   zo_sentinel" is **FALSE** and I acted on it before checking. Verify:   `Get-Service *postgres*` / `Test-NetConnection 127.0.0.1 …

### index_counters_censuses.md
*"HOP-2 index — counters and censuses that lied; open before publishing a count or calling something absent, when the root index's short list doesn't match the symptom"*
Moved from the root index 2026-08-23 (compaction; every link kept verbatim).  - [**TTL ARTIFACT WITH NO WRITER**](a_ttl_artifact_with_no_writer_is_a_countdown_not_a_roster.md) — FU-376. Roster expired at 10.1d; expiry silently collapsed EVERY lane's cadence window to the daily default and false-flagged a weekly lane that ran on time. **Ask who WRITES the gate's input, and whether a fresher copy of…

### index_domain_catalogs.md
*"Second-hop index — moat/scoring trust, deploy/prod/infra, product surface, and loop/builder/architect memory pointers, split out of MEMORY.md on 2026-08-04 to keep the root index under its read limit"*
Split out of `MEMORY.md` on 2026-08-04 (root index had reached 20.1 KB against a 24.4 KB read limit). These are **domain catalogs**, not the hazard list — the read-first scar sections deliberately stayed inline in `MEMORY.md`. Open this file when working in one of the four areas below.  ## Moat / scoring trust  - [200K MET](moat_trust_campaign_final_wave_in_flight.md); [ruling](moat_trust_c…

### index_gates_predicates.md
# HOP 2 — Gates & predicates (moved out of MEMORY.md 2026-09-01 to keep the root index # readable). Same rule as the root: one line per entry, detail lives in the target file.  ## Gates & predicates — the entries that bite less than daily (all still live)  - [**`strict=false` NEVER RE-RUNS A GREEN**](a_required_check_called_no_hollow_ran_the_wrong_one_of_two_predicates.md) — FU-285; **check-run AG…

### index_mechanical_hazards.md
*"Second-hop index — mechanical hazards that bite less than daily. Open before writing a probe, a detached spawn, a flag, or a cross-host command."*
Split out of `MEMORY.md` 2026-08-10 to keep the root index under its read limit. The daily-biting hazards stayed in the root; these are one hop away, not retired. **Every one is still live.** Open the linked file before acting.  ## Probes, controls & the things that fake a pass  - [**DETACHED SURVIVES THE CUT, NOT THE HOST**](a_detached_child_survives_the_transport_cut_but_not_the_host.md) — FU-35…

### integration_surface_strategy.md
*"7/20 design — 70% of orphans declare NO prefix so mounting is undecidable; fix = KL artifacts (route/mount/surface/query) injected not queried, on the existing schema_kl + graph_refresh pattern"*
Strategy for surfacing mount/integration points to arch-goose + builder-goose. Full doc: `D:\zo\Zocomputer Agents\INTEGRATION_SURFACE_STRATEGY_2026-07-20.md`. Follows [[reachability-ratchet-landed]].  **The pattern already exists — `schema_kl.py` is the template.** It (1) introspects live `app.models` mappers, (2) persists `graphify-out/schema_kl.json`, (3) enforces via pure-AST `lint_source()` in…

### key_hydrator_timeout_rung_502.md
*ladder_shim gemini/anthropic rungs 502 when key_hydrator --get times out (30s); fix = relaunch_ladder_keyed (reads /root/.zo_secrets direct).*
The ladder shim self-hydrates LLM keys by spawning `python3 /home/workspace/zo_mesh/key_hydrator.py --get <KEY>` with a **30s subprocess timeout**. `get_secret` falls through: process-env/Modal-alias → local hydrate-file → **request to the Windows tower and wait** for it to drop the hydrate-file. When the tower-side responder is slow, the `--get` is killed at 30s → `RcGeminiAPIKey unresolved` → **…

### kl_unavailable_was_a_32kib_transport_cliff.md
*"fu_context printed \"kl: unavailable\" from the tower for its whole life; the bus was answering all along — zo_call changes representation AND truncates at ~32 KiB, and the reason string discarded the message that would have said so"*
`fu_context.py` always printed `kl: unavailable (direct: URLError; zo_call: RuntimeError)` from the tower, and its own module docstring asserted this as a known fact ("the :8772 bus actively refuses connections from the tower"). The `direct` half is true. **The `zo_call` half never was.** Measured 2026-07-30 against graph `93755e02db32` (`code_nodes`=114,368 / `code_edges`=153,066 on the bus): FU-…

### ladder_attribution_audit.md
*"7/19 audit — ladder wrote 76% of net-new lines but only ~13% of live prod routes; yield ~2.9%; load-bearing conversion tracks Claude mount-points, not directive quality"*
Measured attribution of mcprisky/zo-sentinel (repo HEAD 2026-07-19, live openapi.json as ground truth). Full report: `D:\zo\Zocomputer Agents\LADDER_ATTRIBUTION_AUDIT_2026-07-19.md`, scripts in `evidence/`.  **Discriminator:** all commits are authored `rob531`, so authorship is useless. Use branch `auto/*` + commit prefix `build:` = ladder; `feat:`/`fix:` = Claude session. Both signals agree (626/…

### ladder_green_build_routing_fix.md
*"2026-06-28 the ladder produced its first REAL green build (high_risk_servers_api). Root cause of hollow builds = ROUTING (capable models never reached), not model incapability. 4 PRs."*
2026-06-28: Fixed architect+builder+ladder end-to-end; landed the **first real green build via the ladder** (`high_risk_servers_api.py`, 8870 bytes, self-test PASS, build_artifact emitted → publisher PR).  **Chairman's key correction (right):** the ladder is full of capable LLMs; they weren't incapable, they were **never reached**. Telemetry proved it: all `zo_routed` strong models (gpt-5.4-mini/G…

### ladder_writeservice_contention.md
*"Chairman concern 2026-06-25: architect + builder LLM ladder calls (and write_service access) can clash / need queueing. The clash points, what's already mitigated, and the fix plan (single-flight at ladder_shim + durable sync-writes for the box DuckDB)."*
**Chairman concern (2026-06-25):** make sure architect ladder calls + builder ladder calls don't clash with the write_service or each other; "needs some queueing maybe?" -- correct instinct.  **Clash points (3 shared resources):** 1. **LLM ladder = `ladder_shim:8796`** -- BOTH the architect (directive_gen, ~every 600s) and the builder (goose_runner, per directive) call goose -> ladder_shim -> esca…

### lane_isolation_and_shadow_halt_shipped.md
*"#2416 373e5833 — lane-private worktrees, --heal refuses on shared trees, census halt wired in SHADOW. Arming today would be a NO-OP; the founding case WOULD have fired."*
Chairman: *"a system run on autopoiesis principles with protean tasks would already be implementing A and B and probably running C to check no unexpected outcomes — shadowing the impact up and downstream (D maybe?)"*  **He was right and the criticism was of me.** I asked "A or B?" when both were in-remit, reversible and verifiable, and [[standing_authority_envelope]] says a run that stalls inside …

### launch_plan_rename_flyio_betasignup.md
*"Chairman launch plan (set 2026-06-25, for 2026-06-26+): rename off zo/sentinel (-> mcplookup.ai / mcplabs.ai / mcpcheck.ai, Gemini-SEO'd), prepare a Fly.io package (org token, billing set), build a beta-signup model where the Fly frontend makes a secure call to the PRIVATE zocomputer (which owns signup/mailing-list skills) so a FE bug can't leak signup data, deploy Fly.io tonight/this weekend."*
**Chairman plan (2026-06-25 session close), execute next session:** 1. **RENAME** -- "zo" and "sentinel" overused. Leading candidates **mcplookup.ai / mcplabs.ai / mcpcheck.ai** (Gemini scored all high for SEO). FIRST check domain availability, **CHAIRMAN DECISION 2026-06-25: COSMETIC ONLY.** Brand layer = domain + UI + page titles. Internal "zo sentinel" references in commit history, PR comments,…

### leaning_on_an_assertion_makes_its_negative_control_the_work.md
*"fire_gate's exit code held an 18-run-old prod stage together and had never once been observed returning RESTAGE (FU-173, PR #2294)"*
2026-07-29. `tools/fire_gate.py` answers "would firing today's main build a different image than the one we vetted?" Its answer is an **exit code** — 0 SAFE / 1 RESTAGE / 2 ERROR — and that code is what `prod-drift-sentinel` branches on and what step 2 of the one-click tells the chairman to read.  Citing it let me skip restamping the 8 candidate gates an eighth time on an unchanged tree ([[seven_v…

### left_alive_for_forensics_has_no_owner_for_the_second_half.md
*"A paid instance deliberately spared for forensics bills forever, because the wedge guard is keyed on the transient state and the expensive state is the steady one (FU-365, 2026-09-01)."*
Vast instance `49452453` (`enrichment-ab-v1`, $0.1756/h) billed **12.6 hours** doing nothing and no guard saw it. Its own launcher had written the whole story on 2026-08-31 and nobody read it until the 2026-09-01 audit: `gemini_corpus_eval/ab/fire.err` contains one line, **`staging failed; instance 49452453 LEFT ALIVE`**; `resume.err` is a `TimeoutExpired` on SSH to the pod; `run_resume_49452453/p…

### legacy_directive_generator_retired.md
*2026-06-29 retired the legacy MiniMax directive generator that was starving the goose architect with enrichment churn; goose architect now sole source*
2026-06-29 — ROOT CAUSE of the enrichment churn found + fixed (chairman's "E2E tool vs MCPLookup confusion" hypothesis, confirmed).  **Two architects ran concurrently with OPPOSITE prompts:** - `sentinel_directive_generator_goose.py` (NEWER, Phase-0b; runs goose recipe `goose_recipes/directive_architect.yaml`) — correct: "/app product, enrichment DEPRECATED." - `sentinel_directive_generator.py…

### log_silence_is_not_a_dead_daemon.md
*7/27 — a healthy builder looked 141min wedged because its log inode was deleted; both log-file and /proc/fd/1 probes lie. service_health is the only truth.*
On the zo-sentinel runtime, **log silence is not evidence that a daemon is dead**, and this nearly caused an unnecessary restart of a healthy `goose_runner` on 2026-07-27.  The signature that fooled it: `/proc/6797/fd/1 -> /home/workspace/logs/goose_runner.log (deleted)` with a last write of `06:51:23Z` — 141 minutes of silence on a 60-second cycle loop, process alive (S, 2 threads, 3s CPU, no chi…

### loop_watch_e2e.md
*"loop_watch.py — the end-to-end watcher for the self-improving loop (repo→graphify→memory→directive→goose). Localizes WHICH hop stalled + emails Robin on alert via /zo/notify. Part C of the loop-monitor work."*
**`loop_watch.py`** (repo root, PRs #422 core + #425 canary + #426 email, all merged 2026-06-22) watches the self-improving loop **repo → graphify(code_nodes) → memory(mesh_memory) → directive(proposed/) → goose** and checks FLOW, not just liveness: if an upstream stage is fresh but the next is stale past threshold, it **localizes which hop stalled** + hints the PLAYBOOK fix. Read-only (bus :8772 …

### managed_vast_jobs_shipped.md
*2026-07-02 PR*
2026-07-02 (night): **PR #1180 merged + deployed** — paid GPU jobs are now managed by the E2E (chairman flag: "training jobs run on vastai and don't get managed by the E2E").  **What shipped:** `zo_sentinel/vast_jobs.py` generalizes the SFT repo's proven dispatch_vast_v3 lifecycle into a library: JSON **manifest** (launch spec, cost_cap_usd, max_dph, deadline_min, artifacts, machine-readable check…

### mcplookup_domain_dns.md
*"2026-06-27 custom domain mcplookup.app (Porkbun DNS) — Fly + Clerk-prod DNS plan and the app's Fly IPs"*
2026-06-27: chairman bought **mcplookup.app** (registrar/DNS = Porkbun, Cloudflare-backed authoritative DNS; NS curitiba/fortaleza/maceio/salvador.ns.porkbun.com). Default records were parking: `ALIAS mcplookup.app → pixie.porkbun.com` + `CNAME *.mcplookup.app → pixie.porkbun.com`.  **Fly app `mcplookup` IPs:** v4 shared `66.241.124.183`, v6 dedicated `2a09:8280:1::136:3ba7:0`. Fly certs ADDED for…

### mcplookup_launch_state.md
*"2026-06-26 session-archive — mcplookup.fly.dev is LIVE as an authenticated-only 3-tier SaaS; auth gate + builder re-scope merged; one open item (chairman's own admin role)."*
2026-06-26 session end. **mcplookup.fly.dev is LIVE** as an authenticated-only threat-intel SaaS over the 65,532 SFT scores + 80,539 registry (Fly Postgres `mcplookup-db`).  **Shipped + verified live this session:** - Full nav-shell SPA (Dashboard / Explore[wildcard+filters+pagination] / Submit / Reports / Detail[7-axis] / Admin), History routing, no dead ends (Claude-in-Chrome tested). app/static…

### mcplookup_org_positioning.md
*"mcplookup.org is a PARKED for-sale domain (not a competitor); the real signal is a name/positioning mismatch — \"lookup\" = crowded discovery lane, product's moat is trust/security intel"*
2026-07-01 (chairman asked: eval mcplookup.org, rebrand vs functionality review):  **mcplookup.org is NOT a competitor — it's a parked domain FOR SALE on GoDaddy Auctions** (renders a GoDaddy /lander "this domain is available"). No live product, no github, not indexed, no evidence it was ever a real project. So it does NOT force a rebrand. If anything, a cheap defensive buy to prevent squatting/co…

### memory_mcp_cap_fix.md
*2026-06-29 fixed memory-MCP silently dropping transcripts >8MB; raised cap to 64MB + transcripts now always stream-indexed*
2026-06-29: The Tower memory MCP (`C:\Users\robin\.claude\tools\memory-mcp\server.py`) was **silently dropping every conversation log over 8 MB** — `reindex()` did `if st.st_size > max_bytes: skipped; continue` BEFORE the streaming extractor ran. 5 Cowork `audit.jsonl` files were over cap (largest 27 MB), so whole sessions were never vectorized. This is why a search for yesterday's dispute-fronten…

### memory_needs_predicates_like_the_ledger.md
*"memory has the SAME defect the FU ledger had - unverified prose, no expiry, no predicate - so a rotted fact is indistinguishable from a true one; the fix is the same `- verify:` mechanism"*
Chairman, 2026-07-28: worried that things learned six months ago are being forgotten, and that MEM MCP + graphify were meant to prevent exactly that. Today is the case study, and the diagnosis is sharper than "context is too small":  1. `MEMORY.md` asserted "Tower Postgres PG16.6 :5432". **False.** I acted on    it and told the chairman it was true. Nothing in the system could have    caught it. 2…

### migration_risk_is_a_graph_not_a_file_diff.md
*"Six prod stages classified `alembic upgrade head` GREEN off a path diff against a commit nobody had ever read off prod. A path diff cannot see two heads. #2069."*
`fly.toml` runs `release_command = "alembic upgrade head"` against the prod moat PG on every deploy — no true Fly rollback, one prior failure (v61). Until 2026-07-27 `prod-drift-sentinel` classified that risk by diffing migration **file paths** between prod's commit and main. Two holes:  1. It rested on `prod_approx_commit`, **inferred** from the v64 release timestamp, because    `/version` has se…

### minimax_strategy_eval.md
*"MiniMax key binds to the LATEST model (M3, May 2026) — worth keeping; the lever is decomposition (resurrect directive_simplifier) + fixing a prompt contradiction, not abandoning it"*
2026-07-01 eval (chairman asked: is the MiniMax key on the latest model + worth paying?):  **Binding: YES, latest.** escalation.py LADDER rung 1 (the complexity=medium builder pin, "builder_medium") uses model string **`MiniMax-M3`** via `https://api.minimax.io/v1/chat/completions` (key `MINIMAX_API_KEY`, 560 chars, live — the daemon successfully ran an M3 build today). Web-confirmed: **MiniMax-M3…

### minimax_toolcall_ladder.md
*"Why goose builds ghost — MiniMax emits tool calls as text envelope, not OpenAI tool_calls; ladder adapter tool-call support; PR"*
Goose runs as architect+developer via the ladder shim (:8796 → escalation.py) and acts ONLY on structured OpenAI `tool_calls`. Root cause of the 2026-06-18 100% ghost-build stall: **MiniMax (M2.7/M3) intermittently emits its tool call as native `<minimax:tool_call><invoke name=...><parameter ...>` XML inside `message.content`** instead of the structured `tool_calls` field. Goose then treats the tu…

### moat_backup_infeasible_at_2m_rows.md
*"SUPERSEDED 7/26 — the moat backup was never server-bound; a server-side pg_dump of the whole 2.9GB DB takes ~2 min. The '68 rows/s / infeasible' premise was the proxy tunnel, and the row-cap 'fix' silently skipped the moat behind a green status"*
**THIS MEMORY'S ORIGINAL CLAIM WAS WRONG. Read the correction first.**  **CORRECTED 2026-07-26T22:38Z (prod-drift-sentinel).** `db_backups/backup_zo_sentinel.py` was rewritten to dump **server-side** (`pg_dump -Fc -Z6` on the Fly machine via `flyctl ssh console`, written to `/data`, pulled over `flyctl ssh sftp get`, checksum-verified, then deleted from `/data`). Result: a **284.3MB** valid `PGDMP…

### moat_backup_silence_is_not_a_wedge.md
*"2026-07-27 FU-112/FU-113: the nightly moat backup printed nothing for 7min and was killed 1s after its dump succeeded; run it DETACHED via run_backup.ps1 and read alerts[], not just the exit code"*
**A job that prints nothing is not a job you can supervise.** `backup_zo_sentinel.py` emitted no output until its final manifest, so a healthy 7-minute run looked exactly like a wedged one. On 2026-07-27 I killed a run from that silence — roughly ONE SECOND after the server-side `pg_dump` had already completed with RC=0 (301MB, 0 errors). The kill then stranded a 301MB archive on the prod machine'…

### moat_offsite_closed_github_private_release.md
*"2026-07-27 FU-026 CLOSED: moat backups now push off-tower as GitHub release assets on the PRIVATE repo rob531/zo-sentinel-moat-backups; zo-sentinel itself is PUBLIC"*
FU-026 ("no off-site copy of the moat — DR is single-point on the tower") had been re-emitted as a standing nightly reminder for a week. It is CLOSED, wired, and verified: `db_backups/offsite_push.py` pushes the newest restore-verified backup to the **PRIVATE** repo `rob531/zo-sentinel-moat-backups` as a GitHub **release asset**, tagged `moat-<UTC>Z`. First push verified 2026-07-27T07:30:38Z: 301,…

### moat_rescore_baselines.md
*Latest measured moat baselines + wave state for moat-rescore-weekly; first read for any rescore run.*
Baselines (live `/freshness` 2026-09-01T02:07−04:00, cache_age 0s): scored_servers **296,109**, scores_rows 2,072,763, registry **498,702**, never_scored 202,593 *(raw — NOT a backlog, see below)*, newest_scored_at **2026-08-31T04:51:59**, oldest_scored_at 2026-06-24T15:46:24, corpus_age **1.05d** / SLA 7 → `breaching_sla: false`, keyed policy `fail_closed` verified. Fleet fully recovered from the…

### moat_rescore_weekly_job.md
*"Rescore is a SCHEDULED job (tools/rescore/weekly_rescore.py, delta mode, I1-I5), not an agent firing it after noticing. Ceiling $3. 2026-08-11 baselines + the dam: a FAILED run stays 'newest unfinished' forever and blocks every future wave (FU-321/322, PR #3209)."*
**CofC FATHER ruling 5, 2026-07-14.** The durable half of the [[the-line-enforced-freshness-gate]] fix.  ## The doctrine > "An agent noticed it in a daily review and fired a rescore" **IS THE FAILURE**, not the fix.  Fixing the *data* (running the job) resets the clock. Fixing the *system that lets data go stale* means the clock can never reach day 11 again without something **inside the system** …

### moat_still_mostly_garbage_after_fu108.md
*"7/26: landing the FU-108 wave fixed the PIPELINE, not the DATA. Only ~20.6K of 278.6K scored servers carry real scores; ~258K still wear random-head labels from the 7/18, 7/21, 7/24 garbage waves. The moat's headline risk distribution is still mostly noise."*
**Landing the 7/26 wave fixed the pipeline, not the moat.** Post-import, `overall_risk` across the WHOLE moat reads:  | label | rows | share | |---|---|---| | LOW | 126,234 | 45.3% | | HIGH | 71,311 | 25.6% | | CRITICAL | 66,100 | 23.7% | | MEDIUM | 14,957 | 5.4% |  That is **not** a risk distribution — it is a stratigraphy of the three garbage waves: ~126K LOW from 2026-07-24, ~86K HIGH from 07-1…

### moat_trust_campaign_final_wave_in_flight.md
*"CLOSED 7/27 — moat is 279,116 TRUSTED / 0 DISTRUSTED, and on 7/28 PLAN_200K's 200K-assessed goal is MET at 279,116, 79 days early. Volume and quality closed together."*
**The campaign is COMPLETE. Do not fire a wave for trust reasons.** Final state, measured through the SAME gate the exporter ranks on (`weekly_rescore.cohort_trust` → `score_validity.validate_run_from_histogram`, derived per run, never a hardcoded date list):  - **TRUSTED 279,116 / DISTRUSTED 0.** Both DEGENERATE cohorts (`2026-07-21 06:09:50` 11,095   and `2026-07-24 23:29:53` 125,731) are gone f…

### moat_trust_campaign_ruling.md
*"7/27 chairman ruling: rescore the whole distrusted moat NOW. Actual waste was ~$5-6 not $40, but ~90% of scoring spend bought noise; fixing all 256,826 distrusted servers costs ~$3.33 of $18.78 credit. Fired wave 1. The real fix was teaching the exporter to rank by TRUST not age."*
**The chairman's correction that produced this:** I ended a report with *"one thing you should see: the moat is still ~93% garbage."* That was an escalation of something I had the authority, budget and evidence to decide. Under autopoiesis with Protean tasks there should be **~zero "you should see" emits** on scoring and architecture — the system knows the budget and the goal. Surfacing a decision…

### mtd_spend_number_was_never_real.md
*"ops_audit_state.json was overwritten every run, so \"MTD\" was a 24h delta ($1.68 vs the true $7.90) — and balance-delta goes NEGATIVE across a top-up. tools/ops_audit_state.py, PR #2059."*
The daily ops audit guarded a $25/month vast budget with a number that was wrong in two independent ways, and neither was visible in its own output.  1. **History was never kept.** `D:\zo\runs\ops_audit_state.json` was a single    `{"date","balance"}` object **overwritten every run**, so "month-to-date =    delta since the first entry of this month" could only ever see *yesterday*. 2. **Balance-de…

### my_outputs_path_is_not_the_tower_visible_path.md
*Files I Write to the outputs dir are invisible at that path from Windows-MCP PowerShell — MSIX redirects them under LocalCache\Packages\Claude_pzs8sxrjxfjjc (2026-07-29)*
The session outputs directory is given to me as `C:\Users\robin\AppData\Roaming\Claude\local-agent-mode-sessions\...\outputs`, but `Test-Path` on that exact string from Windows-MCP PowerShell returns **false**. The Claude desktop app is MSIX-packaged, so writes are redirected to:  ``` %LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\...\outputs ```  …

### my_own_ledger_entries_were_invisible_to_the_linter.md
*wrote 5 FUs with an em-dash instead of a pipe; lint parsed 164 headings but 158 entries and flagged NOTHING*
2026-07-29. I filed FU-165…169 as `### FU-165 — title`. The convention is `### FU-165 | title`. `ledger_lint.py` reported **164 headings but 158 entries** and raised **no error** — my five entries were not malformed to it, they were *not read at all*. I caught it only because the entry count hadn't moved.  **Why:** a linter that silently skips what it cannot parse reports green on an empty read. T…

### nominal_check_cve_fix_horizon_council.md
*"2026-07-02 final — NOMINAL check passed (engine fallback firing live); fixed superseded-PR gate risk + CVE identifier retrieval bug (#1186, live-verified INSUFFICIENT); 2nd council ruled vuln-intel horizon (docs/DESIGN_VULN_INTEL_HORIZON.md)"*
2026-07-02 (final check-in): **All NOMINAL** after two found-and-fixed issues.  **Health verified:** architect cycling (rc=0 in 14s, +3), builder building with the **engine fallback live-firing in prod** (goose ghost → `[engine] zo-ladder-nvidia wrote N bytes` — the deep fix works), promoter+janitor clean, e2e-nightly green 3 consecutive days, auto-merge flowing. Queues: proposed 0 live / pending …

### osv_ingest_oom_towerside.md
*OSV npm ingest OOM-killed twice on 1GB Fly machine; now running tower-side vs Fly DB via proxy; vuln kill-switch stays OFF until verified*
2026-07-03: The "verify /tmp/ingest_result.txt" thread resolves: the detached OSV npm ingest NEVER completed — OOM-killed twice on the 1GB Fly machine (fly logs: "Out of memory: Killed process ... anon-rss:~760MB", 07-02 22:21Z and 07-03 16:38Z after my relaunch). /tmp evidence wiped by machine restarts. vuln_advisories/vuln_links = 0 rows.  **Why:** vuln_osv_ingestor.py --live downloads+parses th…

### otx_correlation_and_linker_audit.md
*"Linker audit (287 low = server-side pkg identity gap, not a bug) + AlienVault OTX correlation prototype results and integration design"*
2026-07-03 (chairman-requested): audited the vuln linker + prototyped AlienVault OTX correlation.  **Linker audit — 287 links is a RECALL gap, not a bug.** Pattern = pure exact-match set intersection (vuln_identity.py canonical keys `repo:<host>/<owner>/<name>` + `pkg:<eco>/<name>`; THE LINE forbids fuzzy). Quantified via audit_linker_gaps.py: - Advisory side: 221,885 (99.98% npm); only 29,974 (13…

### over_budget_is_not_flaky.md
*"pr_triage's one 300-PR query 504s DETERMINISTICALLY at 119 open PRs; the retry already existed. #2172/FU-129."*
2026-07-28: the `triage` check had been producing **nothing** — not flaking, producing nothing. FU-129 filed it as "transient GraphQL 504, fix = 5× exponential backoff". Wrong on both counts.  `_gh()` **already** retried 504s with 1/2/4/8s backoff, and the 03:33Z run spent five attempts and 71s on five of them. Measured live: **3/3 failures at ~11.2s**. Cause: 119 open `autonomous-build` PRs, and …

### p1_p2_agent_build_2026_07_06.md
*Agent-built KL P1/P2 targets as PRs*
2026-07-06/07 session (post-steer execution of [[post_outage_builder_steer_2026_07_06]]):  - **PR #1315** ops/host/safe_ff.sh — durable non-destructive ff (backs up untracked colliders to zo_sentinel_state/refresh_backups OUTSIDE repo, auto-stash, merge --ff-only; scratch-repo verified). deploy-runtime-from-main scheduled task prompt UPDATED to use it + zo_call.py fallback. - **PR #1316** freshnes…

### p3_reached_promoter_holds_all_21.md
*7/26 loop reached P3 (0→21 staged) but promoter HOLDs all 21 on liveness; merged unverified (FU-031 100%); T1 struct-met/func-unmet.*
2026-07-26: the autopoiesis loop drove a real P1→P2→P3 transition since the 7/25 P1-STARVED snapshot. It emitted service-shaped `build_service` directives, fanned them out, and MERGED them — 0 staged on 7/25 → **21 staged** now (today's 9 commits = two full fan-outs `server_risk_timeline`+`score_timeline`, PRs #1910–#1918). safe_ff'd runtime to `4bf6489f`.  BUT `tools/promote_staged_to_active.py` …

### paid_job_launch_confirmation_policy.md
*"Chairman standing rule — for ANY paid compute job, always confirm launch reached running state and report cost"*
7/17/2026, chairman: "yes always where we run jobs for cost" — whenever a paid job (Vast, RunPod, any cloud GPU) is launched or relaunched, don't fire-and-forget.  **Why:** A wedged instance stuck in "loading" bleeds budget invisibly (7/17: 45168912 stuck 95+ min pulling image, destroyed at ~$0.55). Budget is tight ([[vast-budget-25-monthly]]).  **How to apply:** After any paid-job launch: (1) ver…

### peer_review_replaced_the_chairman_gate.md
*"2026-08-04 — 4 escalate-only clauses became peer-clearable; adversary must RUN a falsification and fail, revert proven before acting, every lane sweeps"*
**Chairman ruling 2026-08-04, permanent, not an away-window relaxation.** Four clauses moved off chairman-hold and became peer-clearable: `redefining_the_metric`, `irreversible_and_unverifiable`, `auth_config_rewrite`, `new_standing_credentials`. **`data_deletion` and `above_the_ceilings` are FOREVER_HELD** — the mechanism rests on reversibility and an adversary cannot un-drop a table or un-spend …

### perspectives_ask_live_in_prod.md
*2026-07-02 v1.1 Perspectives + v2 Ask + /roadmap DEPLOYED to mcplookup.app (PRs*
2026-07-02 (evening): **FATHER's vision is LIVE on mcplookup.app** — chairman collapsed the timescales ("we have Fable now"); direct-built (agent-built per the builder-rescope doctrine) and deployed same-day, ~10 days inside the two-week differentiator window.  **Shipped (PR #1168 + #1170, deployed, smoke-verified 200s):** - **v1.1 Perspectives:** `/perspectives` UI + `/api/facets` (live facet uni…

### perspectives_askrag_lanes_opened.md
*2026-07-02 PR*
2026-07-02: Began the planned weekly work — **PR #1128 merged + deployed**: the recovered roadmap (council 2026-06-27 FATHER ruling: v1.0 → v1.1 Perspectives (week +1/+2) → v2 Ask-RAG held; **chairman override 2026-07-02: open BOTH lanes now**) is landed as the spec anchor the architect consumes.  **What was emitted where (the KL loop):** - `PRODUCT_SPEC.md` **Appendix F** — 6 Perspectives candida…

### perspectives_product_concept.md
*"Chairman product idea (2026-06-27) for MCPLookup — admin-built faceted \"perspectives\" hierarchies as the deterministic complement to the Ask-RAG query surface"*
2026-06-27: chairman surfaced an idea from an early SaaS tool — **"perspectives"**: admin defines multi-level hierarchies for business dimensions (function / geography / product-service), and as objects are added users assign them (via UI) to a layer in the hierarchy; a perspective is effectively a saved, UI-built mini-SQL filter applied to objects. He framed it as *inferior to but adjacent to* th…

### phase10_refill_and_false_plus0.md
*"7/21 PHASE 10 anchor refill (#1702) unstarved the queue 0→3; and the \"+0 did NOT reach propose_directive\" message is provably FALSE"*
**7/21/2026: queue was STARVED** (proposed=0, pending=1) with the floor logging *"gaps map is EXHAUSTED... This needs a human."* Refilled via **#1702, PHASE 10** — 7 lanes, theme *measurement not breakage*: `propose_directive_outcome_log`, `runtime_checkout_drift_probe`, `builder_rung_hollow_rate_report`, `orphan_router_caller_probe`, `risk_tier_threshold_calibration_probe` (FU-058), `axis_change_…

### phase4_anchor_refill_shipped.md
*"Anchor refilled 7/15: PHASE 4 lanes (#1482, 11 targets) + bridge unblocked (#1483 pydantic dialect, #1484 sys.path pin) → architect +6 in one cycle, floor seeds real specs, builder building. How to top up the queue, verified end-to-end."*
**2026-07-15: factory re-armed end-to-end. PRs #1482 (spec anchor + gaps-map disk truth), #1483, #1484 (bridge). Architect: `proposed_depth 0 -> 6 (+6)` at 02:14Z — first multi-proposal convergence in the creation lane.**  ## The verified top-up recipe (when the floor says "gaps map is EXHAUSTED") 1. Mining trigger: a line containing "directive candidate:" (or "not yet"/"dormant") with a backticke…

### phase5_anchor_refill_shipped.md
*"PHASE 5 anchor refill 7/16 (PR #1532): 7 PLAN_200K instrumentation targets after gaps-map EXHAUSTED 7/15 23:09Z; targeted single-file runtime deploy trick; generator log = deleted inode, read via /proc/<pid>/fd/1."*
**2026-07-16: gaps map exhausted again (~13h after [[phase4-anchor-refill-shipped]] targets all built — the factory eats an 11-target anchor in ~1 day). PR #1532 merged: PHASE 5, 7 targets, all verified SEEDWORTHY pre-ship per the [[phase4-anchor-refill-shipped]] recipe (miner sim + #1475 shape allowlist + recursive tree-free check).**  Targets: registry_growth_snapshot_rollup, registry_growth_pro…

### phase7_anchor_refill_shipped.md
*"7/18: PHASE 7 spec (#1597, 8 post-wave-honesty targets) + cap PR #1596 + #1598 wedge re-land; factory unstarved 12:13Z. Scars: 401-body overwrote runtime spec (bak saved it); CONFLICTING PRs arm auto-merge that can never fire"*
Chairman session 7/18 (~12:00-12:25Z). Gaps map exhausted 11:52Z as predicted by the ~1 anchor/day burn ([[phase5_anchor_refill_shipped]]); pending/ held ONLY stale .bak files — looked non-empty from `ls`, was starved. Shipped: **#1596** drift-cap 200K→400K (Fly-deployed, drift run 24 fired — see [[drift_check_cost_ceiling_232k]]); **#1597** PHASE 7 spec, 8 targets themed post-wave honesty + ceili…

### phase8_refill_reindex_fix.md
*7/19 chairman run - PHASE 8 anchor refill + memory-bounded reindex + zombie janitor SHIPPED; architect converged on fresh anchor; run 30 verified ok (double-confirmed by scheduled task)*
2026-07-19 chairman review outcomes:  **#1624 (merged+runtime-deployed): PHASE 8 anchor refill.** 8 targets: cadence_runtime_trend_report, canonical_family_drift_probe, family_rollup_api, score_change_delta_report, score_change_timeline_api, wave_refresh_verification_report, import_row_delta_audit, ladder_rung_convergence_report. Gaps map had exhausted AGAIN ~24h after PHASE 7 — **anchor burn rate…

### phase9_refill_and_gate_attribution.md
*7/20 —*
**#1669 gate attribution (merged 12:28Z).** The completion chain had 7 ways to reject a build and wrote ONE hardcoded string, `"ghost build: declared output_file was not produced"`, for all of them — a lie for 6 of 7. Replaced the two inline `and`-chains with `_gate_chain()` → `(passed, failing_gate)`; `GATE_REASONS` + `gate_error_text()` write `gate=<key>: <reason>` into `build_provenance.error`.…

### plan_200k_committed.md
*200K MCPs plan merged as PR*
Chairman directive 2026-07-15: scale from 20K goal to **200K assessed MCPs**. Plan drafted, adversarially evaluated, merged as **PR #1492** → `docs/PLAN_200K.md`.  Baseline (2026-07-15): 80,539 rows / 66,565 scored. Universe measured live: npm keywords:mcp=60,109; GitHub topic:mcp=50,181; mcp-server in:name=47,734; mcp in:name=272,809 (junk tail); PyPI mcp=18,452; Glama ~22.8K; PulseMCP ~22.3K.  P…

### plan_200k_intake_is_a_daily_step_function.md
*"registry_rows is a STEP function — one ~900-row burst/day at ~11-12Z, nothing between. A fixed-time sample reads 0 or 2x depending on the clock; I nearly filed a phantom 25h outage."*
`registry_rows` does not grow continuously. It arrives in **ONE batch per day** from the `discovery-harvest-daily` lane, ~900 rows, landing around **11:00–12:00Z**. The hourly histogram is unambiguous: 7/25 915 · 7/26 11:00Z 839 · 7/27 11:00Z 900 · 7/28 12:00Z 931, and **nothing in between**.  **Why:** On 2026-07-28 the count-tracker read registry_rows flat at 465,431 — byte-identical to the previ…

### plan_200k_tracker_nudge_policy.md
*"Daily 200K tracker must end with ONE scar-aware nudge (next lever), grounded in Tower memory-MCP history — never a nudge that violates a known scar."*
Chairman directive (2026-07-15): the daily plan-200k-count-tracker should not just report counts — it must "keep nudging us towards the target" with ONE concrete next-lever suggestion per run, while respecting the scars in Tower memory (memory MCP, group zo-sentinel) and auto-memory.  **Why:** A bare BEHIND/ON-TRACK verdict is inert; but history shows naive acceleration causes the exact disasters …

### post_outage_builder_steer_2026_07_06.md
*2026-07-06 post 24h power-outage health check (NOMINAL) + drained 46 dashboard-churn PRs + steered architect to plan build-targets (freshness + vuln/OTX/CVE)*
2026-07-06: 24h+ tower power outage; scheduled jobs caught up on relaunch. Health check + builder steer.  **NOMINAL confirmed.** Fly app HTTP 200, auth gate enforced (/api/* 401/405, no leak), /scan live, latency ~0.15s. All required CI green (e2e-nightly, app-e2e parity+postgres, axis-reality, full-pipeline, deploy-rehearsal, triage-solid-sweep, codeql). Recurring scheduled tasks all fired catch-…

### powershell_cd_does_not_move_the_dotnet_working_directory.md
*"A CR/LF probe returned bytes=0 CR=0 LF=0 for a 62KB file — PowerShell `cd` moves the provider location, not the .NET process cwd, so a relative path in [System.IO.File] silently resolves elsewhere."*
**2026-08-07, cycle-0019.** Ran, to measure line endings before a ledger edit:  ``` cd "D:\zo\Zocomputer Agents"; [System.IO.File]::ReadAllBytes("_tools\improve_loop.py") ```  Result: **`bytes=0 CR=0 LF=0`** for a 62,627-byte file. No error, no exception, rc=0.  **Why:** PowerShell's `cd`/`Set-Location` moves the *provider location*; it does **not** change the .NET process working directory. Any r…

### powershell_double_quotes_ate_every_dollar_sign_in_a_ledger_write.md
*"A double-quoted PowerShell string silently expanded $0.00 to \\.00 in two FOLLOWUPS.md bullets; the writer reported VERIFIED because it checked presence, not bytes"*
2026-08-04, moat-rescore-weekly. Two FU-237 log bullets were composed in PowerShell as `$m = "...$0.00..."` and piped to `_tools/fu_append_log.py --message-file`. PowerShell expanded `$0` as a variable, so what landed on disk was `\.00`, `\.08` and `` `\` `` — every dollar sign in the entry gone, including the ones stating that a run cost **$0.00**, which is exactly the fact the bullet existed to …

### pr490_491_validation_pending.md
*PR*
PR #490 (strip stale static-schema tail from context bundle) + #491 (graph-aware already-built REJECT with novelty steer in directive_mcp.py) merged & deployed 2026-06-23; runtime restarted 14:45:19 UTC on commit 3eba8a2.  **UPDATE ~15:15 UTC — one post-deploy cycle ran, TIMED OUT, still +0; verdict indeterminate-but-persists.** After two deferrals (14:55), the architect invoked goose at **15:05:1…

### pr_backlog_cleanup_2026_06_27.md
*"2026-06-27 PR triage — all 199 pre-#896 auto-build PRs were hollow; closed 192, merged Dependabot Node24 bumps + Codacy fix; graphify indexes docs/"*
2026-06-27 cleanup of the zo-sentinel open-PR backlog (chairman-directed). Programmatic triage of all **199 open PRs**: **0 classified REAL** (none import `app.db`/`app.models` with the real 7-axis schema). All product-surface PRs stood up their own root-level `FastAPI()`, unwired, with mock DBs or fabricated columns (e.g. #841 used `axis1..axis6`/`overall_score` — real cols are `overall_risk, aut…

### pr_landing_via_windows_mcp.md
*"How to land a zo-sentinel source PR from a Cowork session: drive the Windows tower via Windows-MCP PowerShell (gh is authed) — NOT the 'GitHub Integration' connector (Web-type, no MCP tools in-session)."*
**From a Cowork session, the GitHub connector is NOT usable for PRs.** It shows Connected in settings but Type=**Web** → it's wired for the web Claude.ai UI and exposes ZERO callable MCP tools into the Cowork agent (verified 2026-06-23: ToolSearch for github/pull-request returns nothing). MCP-type connectors (Zo, Gmail, Calendar, Drive) DO surface tools; Web-type ones don't.  **The working path = …

### probe_that_cannot_evaluate_is_not_a_red.md
*"verifier needs THREE states - 0 GREEN, 1 RED, >=2 UNKNOWN; and on cmd.exe a missing binary returns rc=1, so a typo'd probe masquerades as \"bug still present\""*
Any automated checker must distinguish "ran and failed" from "could not run". On the first live fu-verify sweep the write-service bus was not listening and 13 predicates returned non-zero - reporting those as "bug still present" would have been evidence manufactured from nothing.  Two Windows-specific traps found the same hour:  * **`cmd.exe` returns rc=1 for a command it cannot find**, which is  …

### prod_drift_sentinel_cofc.md
*"Standing prod-deploy guard exists — prod-drift-sentinel (CofC 3+FATHER 2026-07-25), Phase 1 stages-for-human, never auto-pushes prod"*
Fly prod (`mcplookup`) had NO self-renewal (deploy-runtime-from-main only renews the ZoComputer runtime) → it drifted 6 days on 7/25. FIXED by standing guard, decided via CofC 3+FATHER.  **Ruling (2026-07-25):** all 3 council members (SRE / Risk / Autopoiesis) voted YES to a guard; FATHER ruled **STAGED-FOR-HUMAN in Phase 1** — the irreversible edge is `release_command = alembic upgrade head` on t…

### prod_fly_deploy_release_manager.md
*"How to ship mcplookup prod (Fly) safely — clean origin/main worktree, alembic release_command, rollback anchor; spine ok:false = drift not outage"*
Shipping prod (Fly app `mcplookup`, hosts mcprisky.io + mcplookup.app) is a MANUAL, human-gated release — every release on record is robin.craib by hand. `deploy-runtime-from-main` renews the ZoComputer LOOP runtime; it does NOT touch Fly prod. Nothing auto-ships prod → prod drifts (found 6 days / ~40 PRs stale at v63 on 7/25).  **Release-manager runbook (proven 7/25, v63→v64):** 1. Token: `python…

### prod_gap_is_a_promotion_backlog.md
*"314 commits behind prod, 83 landing in one run — and NOT ONE can reach prod; read the delta by PATH before escalating it."*
2026-07-28: prod `v64` sat 64.6h stale with **314 commits** on main since its image was built, and main gained **83 more during a single prod-drift-sentinel run** (merge-queue drain, ~1 commit/15s). That reads like a five-alarm deploy gap. Measured by PATH, it is not one:  - `1393591a…a3937da8` — 61 commits / 66 files, **zero** touching `services/active/`, `migrations/`,   or `Dockerfile` (non-sta…

### prod_rc1_cut_state.md
*"2026-06-27 MCPLookup prod RC1 (tag v1.0.0-rc1) — Father's 4 gates, 3 green, HOLD on Gate-3 backup drill"*
2026-06-27: cut **tag `v1.0.0-rc1`** off main (HEAD 7f16a24) as the MCPLookup prod release candidate, executing the council/Father ruling (ship-and-lock current surface, gated). Status of Father's 4 binding prod-flip gates:  - **Gate 1 — DuckDB↔Postgres E2E parity gate:** GREEN. `app-e2e-parity` job (#898) diffs the two snapshots, fail-closed. Validated on dispatched nightly run 28296907751. - **G…

### prod_version_git_sha_was_never_passed.md
*"prod /version served git_sha=unknown its whole life — ARG/ENV/endpoint all shipped, no deploy ever passed --build-arg; fixed by ops/host/deploy_prod.ps1 (#2063)"*
`GET https://mcprisky.io/version` returned `{"git_sha":"unknown","built_at":"unknown"}` on **every release up to and including v64**. Not a missing feature: `Dockerfile` declares `ARG GIT_SHA=unknown` / `ARG BUILD_TIME=unknown`, wires both to `ENV`, `runtime_deploy_info_endpoint.py` is on the COPY list and serves `/version` 200 — and the Dockerfile's own comment says to pass `--build-arg GIT_SHA=$…

### propose_promote_funnel_fork.md
*"THE recurring funnel break since May — promoter promoted directives to a folder goose doesn't watch (silent scanned=0); root cause was a repo-local-vs-tower path FORK. Fixed canonically + tripwire in #347."*
The propose→promote funnel has broken with the SAME shape repeatedly since May 2026: the directive generator proposes (`proposed_depth 0->+N` in directive_generator_goose.log) but `proposed_to_pending_promoter.log` shows `cycle: scanned=0` forever, so 0 reach `pending/`, goose_runner sits idle ("Total directives loaded: 1", that one non-eligible), and nothing builds. Robin's words: "promoters dire…

### prose_ledger_cannot_self_close.md
*"FU entries were 100% prose, so a lint could only enforce SHAPE - the FU-114 \"fix\" appended an empty key and closed nothing; `- verify:` predicates make the ledger a regression suite"*
Measured on FOLLOWUPS.md 2026-07-28: 148 entries, 80 open, 93 (63%) with an empty `- resolution:`, median `- detail:` 1,199 chars, and **0 carrying any machine-checkable acceptance predicate**. That is why the morning lint "fixed" FU-114 by appending a bare `- resolution:` key and closed nothing - every schema field was prose, so shape was the only thing a validator could ever check, and closure a…

### protean_conversion_ledger_trailer.md
*"7/26: the tasks' PROTEAN CHARTER was already correct — it was being contradicted lower down. The real culprit was a shared LEDGER PROTOCOL trailer in 12 files declaring 'the ledger entry is the deliverable', which converted every Protean back into a reporting stub at its own last paragraph. Rewritten to LEDGER + ACT."*
**Chairman asked (7/26) whether prior runs had actually ingested the autopoiesis changes.** Audit of all 13 enabled scheduled tasks: **3 ADOPTED, 7 PARTIAL, 3 OBSOLETE**. The diagnosis was not what it looked like.  **The shared boilerplate was mostly CORRECT.** 12 of 13 tasks already carried a verbatim `PROTEAN CHARTER` header saying *"A task that stalls waiting for approval on work already within…

### protean_self_modification_mechanics.md
*How a scheduled task actually edits its own SKILL and moves scripts to the tower — three paths that look right and are not.*
Mechanics verified 2026-07-28 while the daily ops audit self-modified. Three traps, each of which silently wastes a run:  1. **The live SKILL.md is READ-ONLY to file tools.** `Edit` on `C:\Users\robin\OneDrive\Documents\Claude\Scheduled\<task>\SKILL.md` returns *"read-only in this session (plugin, skill, or knowledge content)"*. The working path is the scheduled-tasks MCP: `update_scheduled_task(t…

### protean_take_the_shape_needed.md
*"Chairman doctrine 2026-07-25 (Robin): be Protean — take the shape the moment needs, then act as that shape fully. Right now the needed shape is EFFECTIVE DECISION-MAKER (decide and drive to done, don't ask permission to report). Other moments call for thoughtful architect, forensic debugger, etc. Never static; read the moment, become the role, own the outcome."*
**Verbatim (Robin, 2026-07-25):** *"you need to take the shape to be the thing that is needed at the right time. The shape you take (Protean) for this point is effective decision maker. In the future you might be thoughtful architect — you're never static."*  **What it means / why.** This is the [[autopoietic_loop_naming]] "substrate is protean" principle applied to MY operating stance, not just t…

### protean_tasks_act_dont_escalate.md
*"Chairman doctrine 2026-07-25 — autopoiesis includes Claude and the scheduled tasks as sub-agents; tasks must be phase-detecting, self-rewriting, and act-authorized, never clock-driven reporters."*
Chairman, 2026-07-25: "Change your shape to make these decisions (protean). Autopoiesis is not just the E2E gateway builder — it's you and the scheduled tasks as sub agents. Never static, always changing when events change."  **Why:** I had merged the keystone, verified the gates, then ended my report by handing back two decisions that were mine to make (the declaration-file refactor and "the depl…

### publisher_dirty_clone_wedge.md
*7/13-15 publisher wedged 2 days by an uncommitted working-tree edit in the pub clone that deleted the saturated-family gate; fixed + self-heal shipped*
**What happened (2026-07-13 13:20Z → 2026-07-15):** an out-of-band edit inside `/home/workspace/zo_sentinel_pub_clone` deleted the saturated-family gate (#1452) from `zo_sentinel/publisher/publisher.py` + its tests — *uncommitted, working-tree only*. Every publish then failed on `git checkout -B` ("would be overwritten by checkout"). Zero PRs after #1464 while builds kept completing. Downstream sy…

### publisher_pr_cap.md
*"The pipeline's real funnel leak — publisher capped at 8 PRs/day (obsolete private-repo Actions guard); raised to 100 in PR"*
The autonomous builder's funnel leak is the **publisher daily PR cap**, not the builder. Measured 2026-06-19 (last 24h via mesh_events/mesh_memory): 66 directives `DIRECTIVE_COMPLETE`, **79 `build_artifact`** emitted, but only **8 `pr_published`** — everything else `deferred_cap` ("daily cap 8 reached" in pr_publisher.log). ~58 completed builds/day stranded.  Root cause: `zo_sentinel/publisher/pub…

### publisher_watermark_frozen.md
*"THE publisher-stage funnel leak (2026-06-23/24): write_service drops the publisher's watermark write -> watermark frozen -> re-scans stale backlog, no new PRs despite builds completing. Fixed by moving publisher state to a local durable file (#507)."*
**Publisher PR-stall root cause (confirmed 2026-06-23, fixed #507).** Symptom: no autonomous build PR for hours even though the architect proposes and the builder BUILDS (build_artifacts emitting). NOT goosetown, NOT novelty — a PUBLISHER bug.  Mechanism (`zo_sentinel/publisher/publisher.py` run_once, REPO at zo_sentinel/publisher/): the publisher reads build_artifacts SINCE a watermark (oldest-fi…

### queue_census_shipped.md
*"The missing organ SHIPPED 7/30 (#2413, aa46d127). Hourly ZoSentinel_QueueCensus. Alarms compare TWO facts. Declare hatch ships EMPTY on purpose."*
Built in answer to [[autopoiesis_has_no_organ_that_reads_the_queue]]. Merged as **#2413 / `aa46d127`**.  ## What it is  `tools/queue_census.py` — per **lane** (an EMITTER, not a topic): `depth`, `opened_24h`, `merged_24h`, `validity`, `silent_for`, `undrained_for`. Lanes with a validator have their **open diffs** judged by the same code the CI gate runs on files: `classify_source()` was extracted …

### queue_janitor_skip_retire_shipped.md
*2026-07-02 durable fix for architect starvation — PR*
2026-07-02: Shipped the "once and for all" fix for recurring "no novel directives" — **PR #1127, merged + deployed + live-verified.**  **Root cause (structural, not episodic):** skip ≠ retire. goose_runner SKIPS dedup-redundant (#1060) and durably-quarantined pending directives but nothing RETIRED them → pending/ saturates → promoter treats squatters as "possibly in-flight" (collision→skip forever…

### ratchet_effective_delta_not_raw_regression.md
*"The reachability ratchet prints \"verdict: REGRESSION\" even when it PASSES — it grades effective_delta (after deferred declarations), not the raw level. Don't panic-read the raw line."*
`tools/reachability_ratchet.py --enforce` prints TWO numbers and the scary one is not the verdict:      verdict: REGRESSION  (orphans=336 baseline=277 delta=+59 mode=enforce)       deferred (declared, unmounted): 59  -> effective=277 delta=+0     exit=0  It grades `effective_delta` (raw orphans MINUS entries declared in `tools/reachability_deferred.json`). On 2026-07-25 I read the raw line, wrongl…

### raw_hash_and_mtime_lie_about_which_copy_is_live.md
*"171 goose entrypoint copies on ZoComputer, 3 live. A raw sha256+mtime classifier produced 43 FALSE 'drifted/newer' flags — all CRLF checkouts. Normalise line endings before hashing; resolve live from /proc."*
**2026-07-29.** Inventory of goose entrypoints on ZoComputer: **171 files, 3 live.**  The live set, resolved from `/proc` and never from a path:  | role | path | |---|---| | builder-goose | `/home/workspace/zo_sentinel/goose_runner.py` (pid 6907) | | architect-goose | `/home/workspace/zo_sentinel/sentinel_directive_generator_goose.py` (pid 8582) — a **1,022-byte `runpy` DEPLOY SHIM**, not the logi…

### reachability_postmortem.md
*"7/19 — why the daily E2E loop never caught 371 orphans; orphan detector returned \"OK-with-orphans\"; hollow gate keyed on the exemplar so the doctrine blinded it; builder DID try to build the mount registry 3x"*
Root-cause of why the closed loop never closed. Full doc: `D:\zo\Zocomputer Agents\REACHABILITY_POSTMORTEM_2026-07-19.md`. Companion to [[ladder-attribution-audit]].  **Exemplar Doctrine is real and ours** — `goose_recipes/module_from_exemplar.yaml` (9KB), `zo_sentinel/build_routing.py`, 3 dedicated tests, `docs/builder_lane.md`, 18 commits, #793 (6/26) → #1000-1003 (6/28). It worked. See [[exempl…

### reachability_ratchet_armed.md
*7/21 CofC armed --enforce at baseline 277 with a declare-or-mount hatch; the ruling was WRONG about who can comply and the gate caught it on its own PR*
**7/21/2026: reachability ratchet ARMED** (#1701, merged `08353a51`). Census forced it: 307 router modules, **31 mounted (flat for weeks)**, 276 orphaned vs a 246 baseline — **+30 in one day** in observe mode, blocking nothing. 268/276 declare real routes, all parse, 259 import the data layer. The drift was caught only because a human downloaded a CI artifact by hand.  **Design (CofC 3+FATHER — se…

### reachability_ratchet_landed.md
*PR*
**PR #1656 MERGED** 2026-07-20 00:05Z, squash → `98ec9f0` on main. Implements the fix identified in [[reachability-postmortem]].  **What landed:** - `tools/reachability_ratchet.py` — global existential invariant: *for every router defined, there exists a mount that makes it reachable*. Pure stdlib static scan. - `tools/reachability_baseline.json` — **246** - `tools/reachability_exempt.json` — empt…

### regression_of_solved_problems_is_the_real_failure.md
*"Chairman 2026-07-25: the adapter-weights loss was a SOLVED problem that regressed — a direct violation of idempotence. The failure mode to guard is FORGETTING, not novelty. Check memory/ledger for a known solution BEFORE re-deriving; verify staging + schema conformance, don't ceremonialise."*
**What happened.** The 2026-07-24 garbage-scores incident (adapter weights never reaching the pod — see [[adapter_gitignore_garbage_scores_rootcause]]) was **not a new problem**. Missing weights / missing evals / missing outputs from SFT jobs had been diagnosed and solved months earlier, back to the RunPod era. The chairman recalled it immediately and unprompted; I did not, and re-derived the root…

### rescore_launch_dir_must_carry_fix.md
*"Fire the rescore from a dir that CARRIES the fix, not the default. On 2026-07-25 D:\\zo\\_fire_main was on pre-fix #1782 (plain git add) — firing from it repeats the garbage-score bug. The FU-093+FU-091 fixes lived in D:\\zo\\_pr_adapter@8dfcc28. Verify git add -f + prefetch present BEFORE launch."*
**The trap (2026-07-25).** The weekly_rescore harness exists in many tower dirs (`D:\zo\_fire_main`, `_pr_adapter`, `_anchor_work`, `_fu*_work`, plus `runs\worktrees\*`). The DEFAULT launch dir `D:\zo\_fire_main` was checked out at a STALE commit (`075e707`, PHASE 13 #1782) whose `ph_bundle` still does a plain `git add score_transfer` + I5 sha-pin against the LOCAL copy — i.e. the exact code that …

### rescore_run_died_midimport_undetected.md
*"7/21 moat rescore found run 20260719-003024 open for 2 days with 65,045 paid preds half-imported; resumed at $0; how a half-done import hides from every counter"*
**2026-07-21 weekly moat rescore. Baselines after: `scored_servers` 172,295 · `scores_rows` 1,206,065 · `registry_rows` 232,206 · `newest_scored_at` 2026-07-21T06:09:50.825 · `corpus_age_days` 0.02 (was 2.03) · `model_version` v3.0_40974559 · spend today $0.00 (run total $1.19, ceiling $3).** GREEN, not degraded.  **What it found instead of a rescore to fire.** Run `20260719-003024` was still OPEN…

### rescore_v1_launched_2026_07_03.md
*Phase-1 full-registry rescore COMPLETE — 66,565 servers scored v3.0_40974559, registry fully tiered (0 unassessed), instance destroyed $1.16*
2026-07-03: Phase-1 rescore FIRED (chairman-approved deviation A: proven fire_score.py path, not the runner).  - **Manifest bug (fixed PR #1217)**: jobs/registry_rescore_v1.json onstart pointed at sft scripts/vast_onstart.sh = TRAINING onstart (no rescore artifacts; all 6 checks would fail after a paid burn). Also removed unfulfillable bar_passes=true check (v3.0 leaderboard bar=false but PROMOTED…

### review_2026_07_08_p1p2_merged.md
*"7/8 48h review: #1315/#1316 MERGED, #1317 conflict-fixed + auto-merge armed; builder churn signals; breaker auto-recovery WORKS"*
2026-07-08 review session outcomes:  **P1/P2 merges (chairman approved):** #1315 safe_ff + #1316 freshness MERGED. #1317 vuln/OTX/CVE surfacing conflicted with #1316 landing (union conflicts in app/main.py MODULES list + evaluator.yml test list — kept both sides), fixed on branch `conflict-fix-1317` pushed to `feat/vuln-otx-cve-surfacing`, **auto-merge armed** — VERIFY IT LANDED next session. Badg…

### review_2026_07_09.md
*7/9 chairman review — trust_synthesiser None-crash fix*
2026-07-09 daily review outcomes:  - **trust_synthesiser bug found+fixed (PR #1368)**: `signals.get(name, 0)` returns None because keys EXIST with None values — `None >= 70` TypeError dropped ~40% of assessments/cycle silently (no verdict written). Fix = `_signal_value()` None-safe accessor; missing ≠ weak. Verify it deployed + errors stopped in trust_synthesiser.log. - **#1217 rescued**: add/add …

### review_2026_07_10.md
*"7/10 chairman review — task-file-loss incident remediated, dispute UI PR"*
2026-07-10 daily review. Root-caused + fixed the missed-chain incident (see [[scheduled-task-files-vanished]]). Backup PASS (465,955 axis rows, flat = no data loss). Runtime ff'd 44 commits → 73f1b7e; safe_ff.sh now on disk (next scheduled deploy can use it — [[safe-ff-script-missing]] resolved in practice). Vast: 0 live/0 open. App: mcplookup.app 301→mcprisky.io canonical, health ok.  PRs: closed…

### review_2026_07_10_pm.md
*"7/10 PM run — CVE/integrity council PR, publisher dup guard, orphan-module finding, SEO strategy"*
2026-07-10 PM session (follows [[review-2026-07-10]]). App healthy (mcprisky.io /health ok). 39 merges on 7/10; the 7/06 steer WORKED — full P1/P2 set (freshness_metadata_api, vuln_facet_extension, vuln_coverage_sla_api, server_threat_intel_view, osv_feed_ingestor #1380) built AND mounted; THE LINE respected.  **Two rots found:** (1) 282/299 root modules ORPHANED (built+merged, never in app/main.p…

### review_2026_07_11.md
*"7/11 review — anchor leaked AGENT-ONLY module into factory (fix #1417); queue starved, 4 CVE-lane directives seeded; architect cerebras rung ~1/15 convergence"*
2026-07-11 daily chairman review (follows [[review-2026-07-10-pm]]). App nominal (mcprisky.io canonical; mcplookup.app 301s by design — check /health with -L). #1411/#1412 VERIFIED MERGED.  **Sensitivity leak found+fixed:** anchor_refill (09:20) mined `vuln_link_expander.py` from the #1409 council doc into factory candidates — an AGENT-ONLY component per #1412. Root cause: the doc kept `.py` token…

### review_2026_07_12.md
*"7/12 chairman review — architect starved 13h on cerebras, rotation fix PR"*
2026-07-12 daily review outcomes:  - **Architect starvation root cause**: pinned zo-ladder-cerebras went +0 (NON-CONVERGENCE) every cycle from 2026-07-11 22:46Z; proposed/ held only 87 `.duplicate` residue (real depth 0, counter globs `*.json` so residue was harmless to the cap); builder idle. Fix = **PR #1437** capable-rung rotation for the architect (mirrors builder #1001): rotate cerebras→nvidi…

### review_2026_07_13.md
*"7/13 review — deploy≠loaded root cause (architect ran pre-#1437 code, reloaded), publisher hollow gate"*
7/13 chairman review outcomes:  - **Deploy ≠ loaded**: #1437 rotation code hit container disk at the 09:09 deploy, but the architect daemon (started 04:20) kept running pre-fix code → cerebras +0 all day, factory starved. Fixed with `tools/reload_daemon.sh sentinel_directive_generator_goose` (pid 38455). Carry-forward: deploy-runtime-from-main should set reload markers for daemons whose source cha…

### review_2026_07_14.md
*"7/14 session — architect repinned nvidia (.zo_env beats go.sh!), /freshness+/version LIVE (#1466, deploy v54), SSH tailnet bridge provisioned, moat 11d stale, edit-task ghost-complete hole"*
7/14 interactive session (Robin present) outcomes:  - **.zo_env OVERRIDES go.sh for wrapper daemons**: daemon_wrapper.sh line 2 sources /home/workspace/zo_mesh/.zo_env AFTER inheriting env — its `export ZO_ARCHITECT_MODEL=zo-ladder-v1` silently clobbered both the go.sh pin and my env prefix. Changing the architect model requires editing BOTH go.sh (line ~383) AND .zo_env (line 17), then killing wr…

### review_2026_07_14_pm.md
*"7/14 PM chairman review: CofC ruled on stale moat → #1467 THE LINE in code + #1468 rescore pipeline + #1469 /freshness 48s→4s, all deployed. Factory found starved (0/0), reseeded, 3 builds clean."*
Automated chairman review, 2026-07-14 PM. **Nothing red.** No email sent.  ## Shipped + DEPLOYED (all verified live, not just merged) - **#1467** [[the-line-enforced-freshness-gate]] — THE LINE enforced in code. Prod verified: `/freshness/policy` → `sla_days:7, breaching_sla:true, keyed:fail_closed, public:fail_visible`. - **#1468** [[moat-rescore-weekly-job]] — `tools/rescore/weekly_rescore.py`, …

### review_2026_07_22.md
*"7/22 chairman review — queue hit empty (genuine exhaustion); CofC-ruled report-only PHASE 11 refill #1736; deferred graveyard auto-growing 5→11/day toward the 40 trigger"*
2026-07-22 daily chairman review. The factory hit `proposed=0 pending=0` (done=2140) — GENUINE anchor exhaustion, not a stale-checkout false-empty (runtime HEAD f798ba9e, only 1 commit behind origin/main) — after PHASE 10's 7 lanes all merged 7/21. Key behaviour confirmed: the architect (nvidia rung) non-converges by emitting `propose_directive` as PROSE and proposing net-new-router + `integrate_*…

### review_2026_07_23.md
*"7/23 chairman review — ran the docketed 07-23 mount-lane CofC (folder-scan + Option-B, human-gated cohort, DEFER auto_declare refusal, mount execution = attended); refilled drained queue PHASE 12 #1751; graveyard 20→40≈07-26; dirty-clone near-miss FU-083"*
2026-07-23 daily chairman review. All GREEN — app nominal (mcplookup.app/mcprisky.io 200, db_reachable), all six daily scheduled tasks ran, plan-200k 7/23 row present (registry 232,244; scored 172,295 SATURATED). Factory daemons all single-PID (no orphans), ladder_shim:8796 ok. Builder provenance last 30 = 15 pass / 7 ghost / 8 failed (losses cluster on the unbuildable `wire_*_into_main` class, no…

### review_2026_07_24.md
*"7/24 chairman review — prod registry DOUBLED overnight 232,245→462,751, VERIFIED real discovery (230,506 fully-distinct, 39 overlap); coverage ~74%→37%; FU-088 CofC raised ask-corpus ceiling 400k→1M (#1781); refilled empty queue PHASE 13 registry_ingest_anomaly_report (#1782); FU-089 deferred hardening"*
2026-07-24 daily chairman review. GREEN + one headline. App ok (mcplookup.app/MCPRisky prod 200). All six daily tasks ran; daemons healthy single-instance (the "promoter: 10" bracket-pgrep = 4 distinct promoters ×(wrapper+py) + probe, NOT orphans); ladder_shim:8796 ok; write_service :8772 ok. ~25 build PRs merged in 24h. Merged #1744 (clean, 2d-stuck, auto-merge hadn't fired); #1780 self-merged. U…

### review_2026_07_25.md
*"7/25 chairman review — keystone #1786 blocked by dead treewalk-smoke; autopoiesis scoreboard never emitted; drained 3 PRs"*
7/25 daily chairman review. Loop ALIVE: all 4 core daemons up ~4h (ladder_shim, goose_runner, sentinel_directive_generator_goose=architect, fingerprint_runner_v3), ~13 build PRs merged. FU-093 (adapter-never-reached-pod, the 3-wk garbage-score root cause) and FU-094 (score-validity fail-closed gate) both MERGED — moat integrity fix landed.  KEYSTONE #1786 (Autopoietic Loop v1: service atomic unit …

### review_2026_07_26.md
*"7/26 chairman review — loop shipping SERVICES (T1 ahead); first valid re-score since 6/24 but UNIMPORTED; backup hardened fail-loud; closed superseded #1820."*
Daily chairman review 2026-07-26. State: **loop healthy and now emitting the SERVICE atomic unit autonomously** — overnight ~30+ `build:` PRs merged as 4–5-file fan-outs (contract/logic/router/init/service.toml: score_timeline, server_risk_timeline, risk_tier_overview, server_risk_summary, org_risk_tier_summary…). Directives proposed 40 (build_service + `.expanded` fan-out live), pending 12, done …

### roadmap_next_session.md
*"Current roadmap / next-session plan (as of 2026-06-23, after the architect namespace fix). The 4 original goals + today's concrete work."*
**Architect REGRESSED to +0 at ~10:18 today (2026-06-23).** It worked this morning (namespace fix [[architect-namespaced-tools]]) — last successful convergence 10:17:52 UTC wrote 4 directives (wire/build threat_intel_ingestor + incident_webhook_dispatcher enrichments). Every cycle since (10:27→12:00) wrote NOTHING. Cause (per directive_mcp.log + goose log): NOVELTY EXHAUSTION / ~5-subject fixation…

### run_your_verify_before_the_change_and_require_it_red.md
*"A verify predicate guarded on its own subject returns rc=0 on a zero-row family, so a no-op and a correct action are indistinguishable and --sweep never auto-reverts; run every verify BEFORE the change and require it RED"*
2026-08-07, deploy-runtime-from-main acting as peer-review adversary. `friction_family_census.py`, shipped as the verify predicate for proposal `scratchpad-silent-nothing-family`, reads:  ```python if fam_rows and not (len(fam_rows) == 3 and len(lanes) == 3):     ok = False ```  It is **gated on the very family it asserts about**. Had the proposal been ACTED with a narrow aliases regex matching ze…

### runbook_heal_ruling_and_the_stagger.md
*"RULING 7/30: runbook_pin DETECT everywhere, --heal NOT unattended on a shared worktree. Schedule staggered, 22 dead tasks deleted (41 -> 19)."*
Chairman asked whether `autopoiesis-bar-tracker`'s action should be allowed, given concurrent tasks. It landed as **#2415 `aef14a6c` (FU-198)** while the eval ran.  ## The finding is CORRECT — independently verified  Its claim: at 13:39:49Z a sibling parked `D:\zo\_runbook` at `ae71dafd` (the *staged candidate* sha, 131 behind main), and 10 min later this task ran tools out of it. Verified from th…

### safe_ff_script_missing.md
*"safe_ff.sh IS on runtime and working (7/16 deploy to 9bc46f8a, 31 colliders backed up); inline fallback = disaster-recovery only"*
**RE-VERIFIED 2026-07-16.** safe_ff.sh ran clean on the scheduled deploy: backed up 31 untracked colliders (the whole risk_tier/perspective/dispute API+view batch) to zo_sentinel_state/refresh_backups/20260716T090920Z, auto-stashed tracked mods, ff'd to HEAD 9bc46f8a (build_ask_corpus_indexer_integration #1526). Exit 0. The 7/15 "script NOT on runtime" note is resolved. Watch: 31 untracked collide…

### scheduled_task_files_vanished.md
*"7/9-7/10 daily-chain miss root cause = 5 SKILL.md task files vanished from OneDrive Scheduled dir, NOT tower asleep; masters live at D:\\artifacts\\Scheduled"*
2026-07-10: db-backup, pipeline-watch, deploy-runtime-from-main, graphify-kl-daily-refresh, vast-jobs-daily-audit all skipped 7/9 AND 7/10 because their folders under `C:\Users\robin\OneDrive\Documents\Claude\Scheduled\` were gone (recycle bin empty — likely OneDrive sync purge). The 7/9 "tower asleep" diagnosis in [[review-2026-07-09]] was WRONG.  **Why:** the scheduler silently skips a task whos…

### scheduler_self_edit_is_unprompted_now.md
*"Task self-modification stalled on an approval prompt because the scheduled-tasks MCP tools were never in settings.json allow. Granted 2026-07-28 in full, incl. delete and cross-task."*
**Root cause of the self-edit stall (2026-07-28):** `C:\Users\robin\.claude\settings.json` → `permissions.allow` lists MCP tools by exact name and `defaultMode` is `auto`, so anything unlisted prompts. None of the `mcp__scheduled-tasks__*` tools were listed. A Protean task rewriting its own prompt therefore blocked on the chairman every time — turning the loop's core self-modification act into a s…

### schema_prm_data_source_guard.md
*2026-06-29 added a schema-PRM rule that blocks reading a known DB table from a .csv (data-source hallucination, e.g. #1063); points builds at ws_query/:8772*
2026-06-29 (chairman spotted in PR #1063 `diagnose_signal_weakness`, which invented `data/mcp_signal_scores.csv`).  **Root cause:** schema-PRM (`schema_kl.lint_source`) linted hallucinated COLUMNS (constructor kwargs / attr access) but not a hallucinated DATA SOURCE. And the repo has **8 modules** that legitimately EXPORT to CSV (compliance_reporter `generate_csv`/`CSV_PATH`, bulk_assess, compli…

### score_dispute_feature.md
*"2026-06-28 user score-dispute backend LIVE (submit + admin review, record-only). Model+API+grants done; frontend UI still TODO. Two deploy gotchas captured."*
2026-06-28: Built the user score-dispute / re-score feature (chairman request). Users dispute an MCP's risk: pick overall risk (LOW/MEDIUM/HIGH/CRITICAL), a STRUCTURED reason_category, a REQUIRED freeform explanation (>=10 chars), and OPTIONAL proposed labels for any of the 6 sub-axes. Admin-gated review/resolve (approve/reject), **record-only** (no score override yet — future job).  BACKEND LIVE …

### score_import_shepherd.md
*"Daily Protean task (10:05, created 7/26 after FU-108) that owns LANDING score waves — the capability that had no owner. Detects stranded waves, diagnoses the four verdict classes distinctly, secures a restore-verified rollback, imports, and round-trip verifies."*
`score-import-shepherd` — `C:\Users\robin\OneDrive\Documents\Claude\Scheduled\score-import-shepherd\SKILL.md`, daily 10:05.  **Why it exists:** producing valid scores and LANDING them are different capabilities, and only the first had an owner. FU-108 cost ~12 hours on a $0.27 run that had already succeeded. See [[first_real_scores_since_0624_blocked_by_fu094]].  **Detects stranded waves** by scan…

### score_run_9min_gpu_heuristic.md
*"Scoring-pod health tripwire — 0% GPU past ~9 min means trouble, BUT only if the log isn't actively advancing (base-model prefetch is a legit 0%-GPU phase)"*
Chairman heuristic (2026-07-26): on a Vast score pod, if GPU is still 0% after ~9 min, something has probably gone wrong — check early, don't wait 20+ min.  **Why:** a healthy pod reaches GPU>0 fairly quickly; a long 0%-GPU stretch usually means a wedge (dead egress / frozen clone / hung import), like instance 45843424 which FATAL:clone'd on a dead-egress host and sat zombie.  **How to apply (PHAS…

### score_wave_onstart_hf_prefetch_works.md
*"WORKING vastai weekly-rescore onstart — HF base-model pre-fetch (FU-091/PR#1790) that fails loud; reuse it. Confirmed GPU 96%."*
7/24: the REFIRED zo-sentinel scoring wave works and is the pattern to reuse for weekly_rescore. Run `20260724-184947`, instance `45732671` (RTX 4090, machine 41211, ~$0.31/hr) confirmed scoring at **GPU 96%** / vmem 13GB, 17m in, ~$0.02 spent against $7 cap.  **What fixed it — FU-091 / PR #1790 (HF-robust onstart):** pre-fetch the HF base model **Qwen/Qwen2.5-3B** with a **hard timeout 600s ×3 re…

### scored_servers_is_blind_to_rescore_days.md
*"2026-07-27: /freshness scored_servers moved +1,085 while the DEFENSIBLE count moved +120,509 — a rescore overwrites in place, so the headline PLAN_200K metric is structurally blind to the most valuable assessment day the project has had. Trusted share 7.4% -> 50.5%."*
**The flattest-looking day was the biggest real advance.** On 2026-07-27 the public `/freshness` surface read `scored_servers 278,026 -> 279,111` (**+1,085**) — the kind of near-flat delta the 200K tracker is built to treat as suspicious. It was not a stall. Moat-rescore wave 1 (fired 02:48Z, 120,509 servers, ~$2.50 cap) **landed at 07:21:18Z**, and because a rescore **overwrites in place** it mov…

### scorewave2_dup_analysis_change_gap.md
*"7/18 ScoreWave2 launch (closeout-first), duplicate-family root cause (92% cross-source; family key is pure inference), and confirmed change-tracking gap in app design"*
**ScoreWave2 (2026-07-18, chairman-ordered in-session):** run 20260717-182921 was still OPEN — phases stopped at `destroy`; the 03:02Z import retry wrote 2 log lines then died silently (0-byte .err; tower sleep/reboot suspected) WITHOUT stamping import/backfill/postcheck, even though the DB rows landed (172,250 scored verified 7/18 AM). So invocation 1 of `Sprint200K_ScoreWave2` schtask = idempote…

### scorewave_184947_adapter_unattached_degraded.md
*"7/24 check-3 — refired score-wave 184947 ran GPU-96% but adapter 401 + random-init heads = degraded output; FU-091 HF fix WORKED, this is a NEW class"*
7/24 score-wave check-3 (run 20260724-184947, inst 45732671, machine 41211). The [[score_wave_onstart_hf_prefetch_works]] FU-091 fix is CONFIRMED working — pod log showed `[prefetch] cached at .../Qwen2.5-3B` then eval_phase2 --device cuda started; old HF-download-hang class dead; GPU 96%, ~$0.18, ETA ~3–5h, well under $7/1078m. wedge_guard 0 refires this run, no HALT, collect watcher owns lifecyc…

### scorewave_push_fail_phase6_refill.md
*"7/17: ScoreWave lost 171,050 preds at push (022858, $2.72); #1564 chunked-push fix; refire 131104 WEDGED (45168912 stuck loading 99min, destroyed ~$0.50) → re-refired as run 151256 on 45176841; PHASE 6 refill #1565."*
**2026-07-17 chairman review.** Run 20260717-022858 (delta mode, 171,050 exported = 105,685 new + 65,365 refresh) completed eval in 6h48m on a Vast 4090 then died at the single `git push` of preds.jsonl.gz — preds lost with the instance, fail-closed correctly (forensics onstart.log captured, destroyed, ledgered, est $2.72). Refire 20260717-131104 fired 13:16Z on instance 45168912 with a hardened o…

### scoring_saturated_db_at_capacity.md
*"7/20 — scoring isn't stalled, it's DONE (families saturated); the real blockers are a 512MB prod PG at 3/3 critical and discovery collapsed to 2/day"*
Chairman asked to "get scoring running" on 2026-07-20. Scoring did not need starting. Three findings, in the order they matter:  > **CORRECTION 2026-07-21 — finding 1 was half right.** Families *are* saturated and `scored_servers` *is* the wrong metric; that stands. But "ScoreWave scored the corpus and finished" was wrong: run `20260719-003024` **died mid-import** and ~32,545 of its 65,045-server …

### security_advisory_feedback_loop.md
*2026-06-29 wired Bandit findings into the builder's closed loop as a SECONDARY non-blocking learning signal (PR #1057), weighted below functional/schema*
2026-06-29 (chairman directive: "wire it up in the matrix; lean toward working functionality + novelty + schema-bound software vs security as the 2nd goal; make it learn"). PR #1057 (merged, deployed to tower HEAD 550b475, goose_runner pid 24827 verified clean cycle).  **Gap:** Bandit findings never reached the builder loop — Bandit runs `exit_zero` + emits SARIF to the GitHub Security tab, and …

### self_check_must_snapshot_before_it_acts.md
*"7/27 — verify_deploy_candidate.py measured tree-dirtiness AFTER running gates that write a tracked artifact, so every clean run cried DIRTY; the tool built to close a trust gap was manufacturing one."*
`tools/verify_deploy_candidate.py` (shipped #2043 to close [[ci_gates_pr_head_tree_not_merged_tree]]) called `working_tree_dirty()` **after** running the gates. `smoke-ladder` rewrites `artifacts/ci_smoke_junit.xml`, which is **tracked** — so every run on a pristine worktree ended with `WARNING: working tree is DIRTY -- this verdict describes the files on disk, not commit <sha>`.  **Why:** a warni…

### sentinel_product_model.md
*"THE canonical product model (resolves the recurring drift): Sentinel = a 3-tier SaaS threat-intel web app. The SFT student model is a DATA INPUT (6-axis risk scores), not the app driver. The Builder is a separate autonomous factory. Anchor everything (PRODUCT_SPEC, novelty engine) on this."*
**Why this exists:** the product description kept DRIFTING (enricher-signal pipeline vs ML risk model vs 3-tier app) because the canonical model lived only in repo docs, not memory. This is the anchor. Confirmed with Robin 2026-06-24.  **Sentinel THE APP = a conventional 3-tier SaaS threat-intel web app** (benchmark its functionality maturity against Anomali / ZeroFox = mature, SSL Labs = lightwei…

### seo_zospaces_metadata_sweep.md
*"2026-07-01 zospaces SEO sweep — the last mcplookup.app lived in per-route SEO metadata (not body), fixed via update_space_settings; site now 0 refs. Plus how zospaces pens/metadata actually work."*
2026-07-01: finished the [[vanity_domains_fly_redirect]] SEO sweep on the zospaces SEO site (publish domain **robinc.zo.space**, "Robin Craib — InfoSec / MCP Security Ecosystem", 75 routes / 49 pages). Verified live via the zo gateway space tools + public curl. **Result: 0 mcplookup references across all 49 routes + 6 pen assets.**  **The non-obvious mechanism (why the prior session's edit_space_r…

### server_identity_url_collision.md
*"A repo URL is NOT a server identity. Tier propagation by URL stamped 14,015 rows (17.4%) with a sibling's risk tier. #1471 deleted it + invariant test. NOT a coverage gap — an identity bug."*
**CofC ruling 2026-07-14 (3 seats + FATHER), R1/R2. PR #1471 (e3a1fc7).**  ## The finding The "~14k never-scored servers" was NEVER a coverage gap. Measured against prod PG: - 80,539 registry rows / **65,552 distinct URLs** - 14,015 rows have no axis scores; **100% share a URL with a scored row**. Zero fail the description filter. Zero eligible-but-unscored. **Rescoring harder never touches them.*…

### seven_verdicts_were_one_confirmation_restamped.md
*"7 verdict artifacts on tree 7fc39201 were byte-identical but for timestamps; cited as \"the SEVENTH independent artifact\""*
2026-07-29, CofC seat 3. `prod_deploy_state.json` cited "the SEVENTH independent artifact on the same tree object" as evidence of strength. Diffing all seven `verdict_7fc39201_*.json`: **identical except `checked_utc` and two gate-duration strings.** Seven deterministic re-runs on an unchanged input is **one** confirmation restamped. The same file had earlier ruled that re-running gates on an unch…

### sixteen_prs_were_born_unmergeable_by_landing_code_in_the_registry_directory.md
*"16 scaffold PRs across 11 days each landed code into services/active/<n>/ with no service.toml, which makes scan_active() see NO_TOML and --strict fail; closing them destroys ~3,500 unique lines."*
**2026-08-10.** Of 113 open PRs, **16 add exactly one file to `services/active/<n>/` (`router.py`, `view.py` or `dashboard.html`) and nothing else** — 2460 2477 2540 2547 2725 2838 2911 2920 2981 2997 2998 3010 3028 3035 3117 3134, spanning 2026-07-31 → 08-10. Every one is titled `scaffold_<n>_service_toml` and **none produces a service.toml.**  **Why each is born unmergeable.** `tools/generate_sp…

### snapshot_runtime_is_unbounded_not_co_residency.md
*"Snapshots went 11m30s -> 64min+ in one day on +0.19% data, running ALONE. Co-residency was a red herring in both directions. FU-139."*
FU-004 was closed 2026-07-27 on the premise "snapshots take 24.3 min at 232K rows and will collide with the reindex" — judged falsified by a runtime *inversion* to 11m30s at 464K rows. The next day (2026-07-28) `perspective_snapshots` run 54 ran **64+ minutes**. The closure was correct on its own evidence and the collision premise really was dead — but the entry had been measuring the wrong variab…

### snow_aidr_future_branch.md
*"SNOW connector + AIDR commit gateway = a deferred future branch (external-3rd-party-app authorization model + logical auth/request segmentation). NOT current work — the architect keeps re-proposing these; don't unblock them now."*
The directive architect repeatedly re-proposes `build_snow_connector`, `build_aidr_commit_gateway`, `build_approval_evidence_bundler` (all `done=True` → promoter skips → contributes to the `novelty_starvation` +0). Robin's call 2026-06-21: **SNOW + AIDR are a future branch, NOT now.** They're useful down the line for sending requests to potential clients / external 3rd-party apps — which needs an …

### soa_atomic_unit_shipped_pr1786.md
*"7/24 SOA new-atomic-unit BUILT (Steps 1-4) as PR #1786 feature/soa-atomic-unit -- fail-loud build-time spine + services/active registry + liveness-gated staged->active promotion; behaviour-preserving, gates green, chairman holds merge"*
**PR #1786** `feature/soa-atomic-unit` (base commit 07edae1c). Built in an isolated ZoComputer worktree `/home/workspace/_pr_soa_atomic` per [[review_2026_07_23]] FU-083 (do NOT author in the tower PRIMARY clone). Implements the [[soa_service_registry_and_db_reframe]] design under the 07-23 CofC ruling (Option B build-time gen, folder-scan, observe→enforce). **Behaviour-preserving for prod; revers…

### soa_service_registry_and_db_reframe.md
*"7/21 SOA design for 07-23 review — staged/active folder registry keeps builder single-file; and the prod-PG \"3/3 CRITICAL\" load interlock is a MISREAD, DB is idle, ~$9/mo fixes it"*
Design doc `D:\zo\Zocomputer Agents\SOA_SERVICE_REGISTRY_DESIGN_2026-07-21.md`, steered into FOLLOWUPS as **FU-072** (P1 umbrella over FU-039/069/070/071/058/053). For the 07-23 mount-lane review.  **render_for_architect() does NOT achieve SOA** — it changes what the architect KNOWS (namespace visibility to allocate a route), not the atomic unit (still a file). It's a component of SOA, not a subst…

### spec_v2_wirein_505_validation.md
*PR*
PR #505 (PRODUCT_SPEC.md Appendix C, 7 Roadmap-v2 app candidates) merged/deployed ~19:07 UTC 2026-06-23, runtime 3f43c7a. Validated 2026-06-23 ~19:40 UTC.  **Result: wire-in confirmed working.** After 19:07 the architect PROPOSED 2 of the 7 app candidates (first time it surfaced real APP work instead of self-introspection): - 19:32:06 WRITTEN `build_directory_presence_signal` → directory_presence_…

### spend_guard_deadline_scaling_kills_runs.md
*"FU-090 spend-guard scaling clamped the WATCH DEADLINE (not just cost) down to 45min for small cohorts; harness default is DEADLINE_MIN_DEFAULT=300. Run 20260725-182808 fired 3576 servers, breached at exactly 45min (pod cold-start+prefetch ate it), collected:[] , self-destroyed for $0.23 zero-yield. First post-FU-093 fire produced NOTHING."*
**2026-07-25 evening (the mess the chairman flagged).** The newly-wired spend guard (FU-090 #1818, `cap=clamp(K*r*N,$0.5,$10)`) also scales the wall-clock deadline. For the small delta cohort (3576 servers = 576 new + 3000 refresh) it produced `cost_cap_scaled: 0.5` AND `deadline_scaled: 45` (minutes). The plain harness default is `DEADLINE_MIN_DEFAULT = 300`. 45 min < pod cold-start + `Qwen/Qwen2…

### spend_guard_was_merged_but_unwired.md
*"The size-scaled vast spend guard (spend_guard.py, FU-090/#1784) was merged to main but NOTHING imported it — weekly_rescore still gated on the flat --cost-cap default. Chairman rule 2026-07-25: gating must scale by JOB SIZE, not an absolute figure. Wired it in FU-090 PR #1818."*
**Chairman correction 2026-07-25 (Robin):** *"gating should scale by the size of job not by an absolute figure — see MCP mem and convo history for this."* I had launched a probe with an arbitrary flat `--cost-cap 0.40`. Wrong on two counts.  **The real defect (uncalled gate).** `tools/rescore/spend_guard.py` shipped in **PR #1784 (FU-090)** — `scaled_budget(N)=clamp(K*r*N, B_MIN $0.50, B_ABS $10)`…

### stale_done_sentinel_retry_gap.md
*"directives/<task>.done.json persists when a build PR is closed unmerged, silently swallowing any reseed of the same task name"*
Directive-factory gap found 2026-07-12: `goose_runner.prune_done_pending()` moves a pending directive straight to done/ when `directives/<resolved_id>.done.json` exists. The sentinel is written when a build completes (PR opened), NOT when it merges — so a hollow build whose PR gets closed leaves a permanent "done" marker for a module that never landed.  **Why:** done ≠ merged. Any chairman/archite…

### stale_trip_pattern.md
*"gate_quality_state circuit breaker stays \"tripped\" indefinitely after a single bad cohort even when all subsequent cohorts pass clean. Has happened twice (2026-05-22, 2026-05-26). Symptom is directive_generator logging \"circuit breaker tripped -- manual reset required\" on files that aren't individually quarantined."*
# Stale-trip pattern (gate_quality_state)  The breaker in `/home/workspace/zo_sentinel/gate_quality_state.json` has a known failure mode where a single bad cohort flips `state: "tripped"` and the state machine **never auto-recovers** even when every subsequent cohort logs 0.0 fail_rate. This has now happened twice in five days:  | date trip began | trigger | reset by | gap | |---|---|---|---| | 20…

### standing_authority_envelope.md
*"2026-07-29 chairman grant, away from 08-07 with very limited input: default stance ACT. authority.json + _tools/authority.py are QUERYABLE, not prose. Prod deploy FIRE_ON_GREEN (Phase 2 early, 5-attended-fire counter waived). Spend $3/wave, $8/week, hard halt $20 MTD. Guardrail hit = halt THAT LANE only, email, others continue."*
The chairman is away from **2026-08-07** with very limited input, and named the real problem: tasks keep asserting "attention or approval" for work already inside their remit, and ownership conflicts get resolved by stalling. His words — *"Tasks will all need to skip approvals and act."*  **Why this is a FILE and not another paragraph.** The Protean charter already said "act". Lanes parked work an…

### stash_triage_org_entity_recovery.md
*"7/19 eve — safe_ff stash stack (17 deep) triaged to ZERO; recovered wired-but-stub org_entity_search_api (240 lines,"*
**7/19 evening — stash triage (chairman-directed), all landed + deployed + verified.**  **Diagnosis:** safe_ff auto-stash stack was 17 deep (back to 7/2), not 3. Contents = recurring daemon churn: ~40 `directives/*.done.json` sentinels + BUILD_STATE/manifest/state files (runtime state in tracked paths) + orphaned/stale builder outputs whose local mods were REGRESSIVE vs HEAD (e.g. weekly_rescore.p…

### tailscale_free_plan.md
*"Tailscale Personal plan as of 2026 — unlimited user devices, up to 6 users, 50 tagged resources, persistent free forever. Zero cost for tower↔ZoComputer bridging."*
Tailscale Personal plan (verified via tailscale.com/pricing and tailscale.com/blog/pricing-v4, search 2026-05-25):  - **Cost: $0, free forever** for personal use - **6 users** (up from 3 previously) - **Unlimited user-owned devices** (the 100-device cap was removed) - 50 tagged resources - 1,000 ephemeral resource-minutes/month - Personal Plus tier retired; consolidated into Personal  **Persistenc…

### the_allowlist_was_not_the_thing_holding_the_button.md
*"2026-08-04 — update_scheduled_task prompts even though it is allowlisted twice with defaultMode auto; SKILL.md IS the store, so writing the file is the approval-free path"*
`mcp__scheduled-tasks__update_scheduled_task` **raises an in-app approval button even though it is in `~/.claude/settings.json` `permissions.allow` TWICE** — by exact name AND via `mcp__scheduled-tasks__*` — with `"defaultMode": "auto"`. Verified 2026-08-04 by reading the file after the chairman reported "everything appeared stuck" and clicked it by hand; the call then failed anyway with ENOENT.  …

### the_anti_friction_guard_was_armed_on_the_path_nobody_takes.md
*"2026-08-05..08-06 — friction.run() correctly REFUSES the ps-command-dollar form and has its own negative control, yet the stall recurred to x18, because every lane reaches PowerShell through the MCP tool, which no Python guard can intercept. RESOLVED SHAPE: reachability, not correctness, was the binding constraint — the constructors now have a CLI (`--spawn`/`--poll`)"*
`loop_health` reports exactly one RECURRING stall: **`mechanical/ps-command-dollar`, x3 in 14 days** — and this lane hit it a **4th** time on 2026-08-05.  The tooling is not missing. `friction.py` carries the hazard **by id**, dated and cited; `friction.ps()` routes through `-File` so `$` survives; `friction.run()` **refuses** the dangerous form outright; and it ships a two-point control proving t…

### the_approval_free_lever_is_the_prompt_not_the_schedule.md
*"During the away window, a lane can rewrite any task's PROMPT approval-free but cannot disable it or change its cron; the no-op prompt rewrite is the only kill switch that works without a human click."*
Measured 2026-08-06, the day before the away window opened.  `_tools/task_edit.py` writes `<Scheduled>/<taskId>/SKILL.md` directly and is approval-free — proven this day on `cadence-jobs-daily-trigger` (16,441 → 18,950 B, byte-verified, backup + `--restore` line emitted). Its surface is `--set-description`, `--set-prompt`, `--append-prompt`, `--restore`.  **What it CANNOT do: enable/disable a task…

### the_away_window_converts_a_stall_into_a_recorded_decision.md
*CLOSED 2026-08-31 (authority.py --away prints "away window CLOSED"; every away-only relaxation reverted on its own). Historical record of the 2026-08-06..08-30 window mechanism (armed a day early on 08-06; the 08-07 date in older prose is dead). away_conduct in authority.json + away_active()/--decision-ref in authority.py. UNKNOWN_ACTION stops RAISING and converts to DECIDE_AND_LOG against a REAL FU; 5 clauses stay HELD; auth_config_rewrite is the only one that moves. MTD halt 20 -> 25. Window auto-expires 08-31. Never read the date from prose -- run `authority.py --away`.*
**WINDOW CLOSED — verified 2026-08-31 by daily-chairman-review: `authority.py --away` prints "PRESENT: away window CLOSED (2026-08-31 > 2026-08-30); it expired on its own and every away-only relaxation reverted with it". UNKNOWN actions RAISE again; DECIDE_AND_LOG is over. Everything below is history of how the window worked.**  The 07-29 envelope fixed the DELEGATED half: in-remit work acts inste…

### the_bar_csv_machine_writer_erases_any_graded_row_it_shares_a_date_with.md
*FU-353 sequel — zo-box daemon write_row drops ANY same-date CSV row; graded rows survive only by launch order; proposal pending adversary*
Proven 2026-08-31 (probe, both poles + control, rc=1 on both hosts): `tools/autopoiesis_bar_tracker.py::write_row` on the zo box filters `r[0] != date` with no phase check, so its daily MEASURED-ONLY row erases a graded P4 row for the same date. The daemon's `cycle()` fires immediately on restart (no initial sleep, interval 86400), so any reboot/restart between the graded write (~13:26Z) and 00:00…

### the_blocker_was_an_unfinished_wiring_not_a_missing_privilege.md
*"FU-235's three options all delete a deliberate least-privilege control; OWNER_DATABASE_URL was already a deployed secret owning `users`, and the repo README already prescribed the one-line fix"*
FU-235 (2026-08-02) reads as "alembic runs as `mcplookup_app`, which owns neither `users` nor `orgs`, so migration 0011 cannot ALTER them" and offers three options, **all of which move ownership of `users` to the app role.**  On 2026-08-03 the prod-drift lane measured two facts that invert the problem:  1. `flyctl secrets list --app mcplookup` shows **`OWNER_DATABASE_URL` STATUS=Deployed**.    Pro…

### the_box_runs_two_schedulers_and_every_sweep_enumerated_one.md
*"The ~01:30 prod-PG tunnel leak went 16 days \"origin unidentified\" because every search enumerated Claude scheduled tasks and the culprit was a Windows Task Scheduler job."*
2026-08-06, FU-057. A `flyctl proxy 15432:5432 -a mcplookup-db` orphan recurred at **01:30:0x local** — 7/21 @01:30:09, 7/22 @01:30:07, 7/23 @01:30:08, 8/6 @01:30:08 — and the ledger carried "origin unidentified" through **five log lines by two lanes over 16 days**.  **The origin was one command away the whole time.** `schtasks /query /fo CSV /v` → `\ZoSentinel_RegistrySync`, Schedule Type Daily, …

### the_builder_goose_session_store_is_isolated_and_decoyed.md
*"FU-085 root cause — the builder's corrupt goose session DB is under ZO_BUILDER_GOOSE_HOME=/home/workspace/.goose_builder, NOT ~/.local/share/goose; there are 6 stores and the obvious one quick_checks clean"*
2026-07-30. Every build died on `Error: error returned from database: (code: 11) database disk image is malformed` — **468 occurrences** in `goose_runner.log`. FU-085 had carried "no detector" since 2026-07-23.  **I checked the wrong file first.** `~/.local/share/goose/sessions/sessions.db` is 644 MB and untouched since Jul 23 — an obvious culprit. `PRAGMA quick_check` → **ok**.  **Resolve from th…

### the_builders_worst_family_was_spelling_and_the_directive_taught_it.md
*109 of 124 names imported from app.models do not exist across 370 modules — mostly MISSPELLINGS of the real 14 classes; and the DATA ACCESS paragraph itself taught the top 27 REDs*
2026-08-09. 250 RED self-tests in one day, 0 GREEN. Census over **2367 tracked .py**: **124** distinct names imported from `app.models`, **109 of which do not exist**, across **370 modules**, against the **14** classes actually defined.  **The finding that reframes it: the dominant family is SPELLING, not absence.** `MCPServerRegistry` (158 files) vs the real `McpServerRegistry`; `MCPLLMAxisScores…

### the_byok_provider_died_and_zo_silently_paused_every_agent_using_it.md
*"Zo's byok:186a2552-71e8-4edb-a844-6d43e4b1bead provider is dead; Zo auto-pauses (active=False) any agent pointed at a dead model, which is a DIFFERENT failure from the credit cap"*
Proven by direct probe on 2026-08-03 23:16Z. I created a disposable automation on `byok:186a2552-71e8-4edb-a844-6d43e4b1bead` and it did not run. It returned a **distinct** error from the credit cap:  - Dead provider → *"Automation paused: its AI model is no longer available"* and Zo flips `active=False`. - Credit cap → *"Automation skipped: Your AI usage is currently limited"*, `active` unchanged…

### the_ci_database_has_one_owner_and_prod_has_two.md
*"deploy-compat runs `alembic upgrade head` as a role that OWNS every table, so it is structurally blind to prod's 8/11 ownership split — its green on a migration touching the original eight is agreement, not evidence"*
`deploy-compat.yml` is the ONLY thing in the repo that builds the real image and runs `alembic upgrade head` in it. It is the pre-flight the prod-drift lane dispatches when a candidate's migration set moves. **It cannot reproduce the failure that matters.**  From its own log (run `30764330226`, 2026-08-02):  ``` -e "POSTGRES_USER=zo" -e "POSTGRES_PASSWORD=zo" -e "POSTGRES_DB=zo_app" postgres:16 ``…

### the_clerk_fix_has_a_second_blocker_that_looks_like_the_first_one_failing.md
*"CLERK_WEBHOOK_SECRET is Staged-never-Deployed, so fixing FU-235 flips /webhooks/clerk from 404 to 503, not to working — and the nightly reconcile that would catch it has no host it can run on"*
Measured 2026-08-03 from the clerk-sync lane. Two facts, both filed, neither previously anywhere in FOLLOWUPS.md or `prod_deploy_state.json`:  **FU-238 — the second blocker.** `flyctl secrets list -a mcplookup` shows `CLERK_WEBHOOK_SECRET` as **Staged**; every other secret reads Deployed. Confirmed from the running machine: one `python -c` returned `CLERK_SECRET_KEY True`, `DATABASE_URL True`, `CL…

### the_clerk_reconcile_has_a_working_driver_do_not_rederive_it.md
*Nightly Clerk reconcile runs via a staged detached driver on the Fly machine; the b64 token quote-escaping was re-derived at 4min cost on 2026-08-24*
The nightly Clerk reconcile (lane clerk-signup-reconcile-nightly) cannot run tower-side: `fetch_secret.py clerk` is AGENTVAULT_MISS by design (option B of FU-239 was refused — no prod Clerk key as a standing tower credential). The decided host (FU-239, chairman decision D3, option A) is INSIDE the Fly machine via `flyctl ssh console -a mcplookup`.  **Working driver: `D:\zo\Zocomputer Agents\_stagi…

### the_control_installed_to_catch_a_silent_stall_returned_a_silent_zero.md
*"peer_review.py BASE is a Windows literal: the READ half returned a false zero (fixed 08-07), the WRITE half silently CREATED a phantom store under the cwd (fixed 08-08) — always run it Windows-side"*
FU-278, measured 2026-08-07T14:37Z. `_tools/peer_review.py:81` hardcodes `BASE = Path(r"D:\zo\Zocomputer Agents")`. Run from the **Linux mount** that path is absent, `_load()` returns the line-107 empty default, and `--status` prints `no decisions on record` and **EXITS 0** — while the real store held 10 decisions (1 ACTED, 1 CLEARED, 3 PROPOSED, 4 FALSIFIED).  **Why it matters more than a path bu…

### the_cowork_scratchpad_path_does_not_resolve_in_the_tower_shell.md
*Files written to the Cowork outputs scratchpad are invisible to Windows-MCP PowerShell — Get-Content returns nothing and the append writes ZERO bytes while printing success*
Staging ledger text in the Cowork outputs directory and then reading it back from the tower shell **silently produces an empty string**. `Test-Path` on the full `C:\Users\robin\AppData\ Roaming\Claude\local-agent-mode-sessions\...\outputs\<file>` returns **False** from Windows-MCP PowerShell: the scratchpad is a session mount, not a path the tower can see.  **Why:** `Get-Content -Raw` on the missi…

### the_cron_is_the_discriminator_not_the_metric.md
*"events_queued=0 + scores_newer_than_index=false looked like a dead scoring lane; moat-rescore-weekly is TUESDAY-ONLY, so zero was nominal — the discriminator was the cron, not the surface"*
2026-08-01, cadence-jobs-daily-trigger. Three readings arrived together and pointed at a dead scoring lane: `events_queued: 0` on the snapshot job, `max_last_assessed` FROZEN at 2026-07-30T01:17:57 (byte-identical to the value the 07-30 run reported, ~48h of no movement), and registry +927 (467,305 → 468,232) proving intake was still alive. Discovery arriving while scoring stalled is a textbook [[…

### the_dark_tool_census_could_not_see_a_sibling_import.md
*"dark_tools.py could not see a sibling import (08-05) nor `from tools.X import ...` (08-11), so it called the acceptance-bar gate and the fleet's #1-ranked dark tool unconsulted — and a census defect in the top slot manufactures work for the loop that reads it"*
2026-08-05, score-import-shepherd. `dark_tools.py` reported **21 dark of 89**. Two of them were `tools/rescore/score_validity.py` — **the acceptance-bar gate** — and `tools/rescore/spend_guard.py`. Both are imported by `weekly_rescore.py`.  Cause: every import alternation in the matcher required the **`tools.` package prefix** (`from tools.X import`, `import tools.X`, `python -m tools.X`). A modul…

### the_dark_tool_cycles_only_winning_move_bricked_its_own_floor.md
*"improve_loop graded a ONE-ITEM-PER-CYCLE engine on an ALL-ITEMS metric, and the only state satisfying it made dark_tools --self-test (a FLOOR member) go red, so succeeding would have stopped the loop forever"*
2026-08-05, FU-260, cycle-0003. Every `dark_tool` candidate carried the FLEET-WIDE predicate `dark_tools.py`, which exits 0 only when the dark-and-unexplained population is **EMPTY**. Two independent failures stacked:  1. **Scope mismatch.** `improve_loop` is hard-limited by its own design to ONE ITEM PER    CYCLE, yet was graded on ALL items. With 17 dark tools the predicate was unreachable    by…

### the_deploy_stash_silently_reverts_runtime_state.md
*"safe_ff's auto-stash is correctness for tracked SOURCE and a silent revert for tracked RUNTIME STATE — git cannot tell them apart"*
`ops/host/safe_ff.sh` auto-stashes tracked local modifications before the ff. For source that is correct. For **runtime state that was accidentally committed**, the same stash is a silent revert: whatever the runtime deleted comes back at the next deploy.  Observed 2026-07-29 (FU-162): 529 `directives/<id>.done.json` **terminal sentinels** are tracked in git — `.gitignore` covers only the `directi…

### the_detacher_was_pinned_by_the_stderr_capture_a_previous_fix_added.md
*"FU-062 2026-08-09 — Start-Process -RedirectStandard* pins the caller for the child's FULL lifetime; the \"detached\" launcher never detached, and redirecting stdout too is a convincing non-fix"*
`tools_cadence_launch.ps1` promised "always returns fast" and instead pinned its caller to the child's full lifetime (fire 80s, watch 61s), burning a Windows-MCP call at the ~60s ceiling on **every** cadence run. `mcp-timeout-orphan` was the fleet's #2 recurring hazard (x16 / 5 lanes) and this one call site was emitting 2/day **by construction**, under a comment claiming the opposite.  **Cause:** …

### the_easiest_proof_covers_the_case_you_dont_need.md
*"The rollback anchor's existence was proven from `flyctl machine list` -- airtight, and structurally only ever true for the image already running, which is the one case rollback never needs. FU-191, PR #2410."*
The staged prod one-click sequence named a rollback image for five days (since 2026-07-25) and nothing ever proved that image could be fetched. Two attempts each stopped one step short:  - **A release record NAMES an image.** `flyctl releases --json` will keep printing an `ImageRef` for a release whose layers are gone. A name is not a manifest. (Field is PascalCase `ImageRef` — a lowercase read re…

### the_engine_has_no_memory_of_what_it_already_attempted.md
*"improve_loop has no dedup and no cooldown, so a permanently-red candidate is selected again and again — cycle-0007 and cycle-0008 are the same item 3.5h apart, and 0001–0004 were the same target four times"*
2026-08-06, improvement-loop cycle-0008. `improve_loop.py --select` ranks candidates from evidence every run and **remembers nothing about what it already handed out**.  - `cycle-0007` selected `loop_degradation / loop_health` at `02:55:07Z`; `cycle-0008`   selected the **identical item** at `06:25:17Z`, 3.5h later. - `cycle-0001` through `cycle-0004` were all `dark_tool tools/anchor_self_refill.p…

### the_engine_turned_over_for_six_cycles_without_ever_firing.md
*"FU-262/263 cycle-0006 — select() graded only cs[0], so one permanently-green top candidate silently ended the run for all 8; fixed with a classifying walk, and the first cycle it handed out found a 20KB tool dark because its requirement lived in a docstring"*
2026-08-05, chairman: *"lets start the engine you need to seesaw it."* Three engine defects, all closed in code; then the repaired engine ran a full cycle end to end.  ## The engine could not hand out work  `select()` graded **`cs[0]` only** and returned. The top candidate was `recurring_friction ps-command-dollar` (score 85), whose predicate is `friction.py --self-test` — **a FLOOR member**, gree…

### the_envelope_mandates_a_class_the_linter_refuses.md
*"authority.json's decide_and_log_contract says to file away-window decisions as `class: decision`; ledger_lint.py accepts only defect/directive/learning and raises E5 on every one. The away window opens 2026-08-07, when EVERY unclassified action converts to DECIDE_AND_LOG. Measured 2026-08-04."*
`authority.json.away_conduct.decide_and_log_contract.lands_in` says verbatim: *"FOLLOWUPS.md, as an ordinary FU via `_tools/fu_ledger.py`, **class: decision**"*.  `_tools/ledger_lint.py` accepts **only** `defect` / `directive` / `learning`. Measured 2026-08-04: two entries filed under that instruction (FU-251, FU-252) both raised **E5**. Corrected to `directive`; lint CLEAN at 246 entries.  **Why …

### the_field_every_other_guard_protects_had_no_validation.md
*"shadow_decision.py --would-fire accepted any string and defaulted to False (a HOLD), so a forgotten flag scored as a C4 safety agreement; five anti-gaming guards had been built around the one unvalidated field"*
`tools/shadow_decision.py` read the C4 decision as `(a.would_fire or "").lower() in ("yes", "true", "1")`. An **ABSENT** `--would-fire`, a typo (`y`), or any unrecognised word all collapsed silently to `False` — stored as a **deliberate HOLD**. `reconcile()` grades `would_fire == human_fired`, so that phantom hold became an **AGREEMENT** the moment the chairman also held, and `--status` counted it…

### the_fleet_was_obeying_its_store_not_ignoring_the_constructor.md
*"inline-interpreter-source survived four cycles because seven SKILL.md prompts handed lanes the unsafe python -c as a copy-and-run exemplar, while zero of thirty-five named the cure"*
FU-318, improvement-loop cycle-0036, 2026-08-10. `--pysrc` (the cure from [[every_cure_existed_and_none_was_reachable_from_a_shell_prompt]]) was armed 06:33Z and **seven more lanes were bitten in the next 13 hours**. Its falsification date was 2026-08-17; the falsification arrived the same day.  Scan of the live store (`<Scheduled>/<taskId>/SKILL.md`, 35 files): **7 carried a literal `python -c` e…

### the_fly_app_name_is_not_the_canonical_host.md
*"The Fly app is `mcplookup`; the canonical host is CANONICAL_HOST=mcprisky.io. mcplookup.app 301s to it, and a 301 DOWNGRADES POST to GET -- so any webhook, callback or POST integration pointed at mcplookup.app fails silently. Measured 2026-08-02. Two cadence SKILLs already said this and no other lane could see it."*
**The app name and the host are different strings and always have been.** Fly app `mcplookup`; `CANONICAL_HOST=mcprisky.io`; `APP_NAME=MCPRisky`. 22 hostnames hold certs on the app ([[mcplookup_domain_dns]]), which is why the name is a bad proxy for any of them.  Measured from inside the Fly machine, 2026-08-02, redirects deliberately NOT followed:  ``` https://mcplookup.app/health          -> 301…

### the_followups_ledger_is_not_in_numeric_order.md
*"FOLLOWUPS.md entries are not stored in numeric order, so the last heading by file POSITION is not the highest FU number"*
2026-08-09, cycle-0031. Picking the next FU number by reading the **last five headings by file position** gave FU-301, so the draft claimed FU-302. Its own collision guard refused: FU-302 and FU-303 had both been filed earlier the same day by other lanes, and they sit **physically ABOVE** FU-300/301 in the file. **Position is not rank.**  Derive it, never read it off the tail:  ```python heads = […

### the_harness_discarded_the_architects_converged_work.md
*"3 of 4 cycles emitted valid directives that were thrown away, then the floor declared the anchor EXHAUSTED and asked for a human"*
2026-07-29. `run_goose_cycle()` read `proposed_delta<=0` as "the model failed", logged the transcript, and DISCARDED it. Basis: `/home/workspace/logs/sentinel_directive_generator_goose.log`, 4 cycles 11:01Z–11:56Z — 3 ended `zero_proposed`. The starvation floor then declared `gaps map is EXHAUSTED ... this needs a human: extend PRODUCT_SPEC`, and the builder logged `Total directives loaded: 0` for…

### the_hazard_detector_could_not_see_two_thirds_of_its_own_family.md
*"2026-08-06 cycle-0011 — the fleet's #1 recurring stall fired only 4 of 13 times against friction.hazards(); both tests required a literal \"-Command\" token that two thirds of the real instances never contained"*
`ps-command-dollar` survived a correct constructor for 15 instances across 9 lanes. The reason was not that lanes ignored the fix — it was that **the guard never told them.** Measured before changing anything, by reconstructing the command form each of the 15 ledger rows describes: **4 of 13 fired. Nine were invisible.** Both hazard tests required the literal token `-Command`, and most real instan…

### the_improvement_loop_never_once_executed_a_predicate.md
*"improve_loop ran predicates as subprocess.run([\"cmd\",\"/c\",pred]); Windows re-quoting made EVERY predicate fail to launch with rc=1, the same value as a genuinely RED predicate, so no cycle could ever close"*
2026-08-05, FU-260, cycle-0003. `improve_loop._run` executed predicate STRINGS as `subprocess.run(["cmd", "/c", pred])`. Windows `list2cmdline` re-quotes list argv, so a predicate opening with a quoted interpreter path arrived as `\"C:\...\python.exe\"` and cmd answered *is not recognized* with **rc=1**. Every predicate this engine writes opens with a quoted `sys.executable`, so **the loop had nev…

### the_kl_drops_files_silently_and_nothing_ever_counted_the_drops.md
*"8 of 2390 tracked .py are absent from the graph; CAUSE FOUND 2026-08-10 -- every scheduled build is `graphify update` (incremental) and no lane ever runs the full-build verb, so a file missed once is missed forever"*
2026-08-05, graphify-kl-daily-refresh. The KL is the loop's **self-description**. **8 git-tracked `.py` files appear in ZERO graph nodes** at the same HEAD they exist at on disk: 5 under `archive/`, plus `create_auth_tokens.py`, `tests/test_fly_token.py`, **`tools/fly_token.py`**. Confirmed by two independent probes with a passing negative control.  **CAUSE FOUND 2026-08-10 (was recorded NOT FOUND…

### the_kl_flattens_nested_ids_without_the_directory.md
*"Graphify KL node ids drop the leading directory for nested files, so any path-derived census of graph.json reports subdirectory files as missing — I nearly published 28 phantom dropped files."*
2026-08-08, graphify-kl-daily-refresh. `graphify-out/graph.json` holds **210,051 nodes** keyed by a FLATTENED id, and the flattening **omits the leading directory** for nested paths:  - `tools/fu/fu_ledger.py` → `fu_fu_ledger` - `tools/rescore/spend_guard.py` → `rescore_spend_guard` - `tools/graph_refresh.py` (one level) → `tests_test_graph_refresh` etc.  So the obvious mapping — `path[:-3].replac…

### the_leak_audits_all_clear_was_identical_to_blindness.md
*"The paid-GPU leak audit returned {live_instances:0, alerts:[], ok:true} rc=0 with a COMPLETELY INVALID key — byte-identical to a real zero-leak day. `vastai` exits 0 and puts the 401 on STDERR; three layers each coerced unknown into zero. FU-192, PR #2411, squash 17cea7c6, armed 2026-07-30."*
The one component whose entire job is noticing spend could not tell *"nothing is burning money"* apart from *"I cannot see anything at all."*  `vastai show instances --raw --api-key <invalid>` exits **0**, prints **nothing** to stdout, and puts the envelope on **stderr**: `{"error": true, "status_code": 401, "msg": "Invalid user key"}`. `rc != 0` was `_cli`'s only failure test, so three layers of …

### the_ledger_flipped_to_100_percent_crlf.md
*"FOLLOWUPS.md was 100% LF on 2026-08-02 and is 100% CRLF on 2026-08-03 — a writer rewrote every line ending, so the documented write recipe was wrong before it was used."*
Measured 2026-08-03T14:45Z: `FOLLOWUPS.md` = 1,503,912 B, **CRLF pairs 3,612, LF total 3,612** — every newline is CRLF. On 2026-08-02 the same file was recorded as **100% LF, 0 CRLF pairs, 1,404,629 B**, and that fact was written into the autopoiesis-bar-tracker prompt as the write recipe.  **Why:** something between the two runs rewrote the whole file (a Windows-side tool, or the sanctioned write…

### the_line_enforced_freshness_gate.md
*"THE LINE is now CODE not prose: freshness_gate.py (#1467) fail-closed keyed / fail-visible public, ONE SLA number (7d). Root cause of silent staleness: an uncalled gate helper with a 30d default."*
**CofC ruling 2026-07-14 (3 seats + FATHER), shipped same day.**  ## The bug that caused 11 days of silent staleness `freshness_metadata_api` had an `is_fresh()` "gate helper" that **NO CALLER EVER CALLED**, with `DEFAULT_SLA_DAYS = 30` — while the operational SLA (pipeline-watch, council doctrine) was **7**. So 11-day-old scores were reported `FRESH`. Two SLA numbers, silently diverged.  **Lesson…

### the_linter_and_the_writer_import_different_copies_of_the_same_module.md
*2026-08-05 — fu_ledger.py exists in 20 on-disk copies at 7 sizes under D:\zo; ledger_lint imports _tools (14049B) while fu_append_log resolved from D:\zo\_lanes\prod-drift\tools (12607B) in the SAME session*
Measured 2026-08-05 by `deploy-runtime-from-main` (`_tools/evidence_classgap_20260805.py`, basis: `os.walk` of `D:\zo` excluding `.git`): **`fu_ledger.py` has 20 copies at 7 distinct sizes** — 9220 / 9445 / 10088 / 10343 / 11413 / 12607 / 14049 bytes.  In one session, for one ledger:  - `ledger_lint.py` imported `D:\zo\Zocomputer Agents\_tools\fu_ledger.py` (14049B) — the copy that owns `VALID_CLA…

### the_live_skill_lives_in_onedrive_not_d_artifacts.md
*The live scheduled-task prompt is the SKILL.md path reported by list_scheduled_tasks (OneDrive\Documents\Claude\Scheduled\<task>\SKILL.md) — D:\artifacts\Scheduled is a stale mirror*
To edit a scheduled task's live prompt, resolve the path from the RUNTIME: `list_scheduled_tasks` returns a `path` per task, and that file **is** the prompt.  For `prod-drift-sentinel` that is: `C:\Users\robin\OneDrive\Documents\Claude\Scheduled\prod-drift-sentinel\SKILL.md`  **`D:\zo\artifacts\Scheduled\...` and `D:\artifacts\Scheduled\_presStagger_*\ClaudeScheduled\...` are STALE MIRRORS.** On 2…

### the_lock_exists_and_nothing_uses_it.md
*"23 of 33 scheduled SKILL.md write shared state. ZERO reference fu_lock.py. The primitive was built 7/28 WITH TESTS after a real clobber, and no actor adopted it."*
Concurrency eval, 2026-07-30, asked for before wiring the census halt.  ## The headline  `D:\zo\Zocomputer Agents\_tools\fu_lock.py` is a correct, tested mutual-exclusion primitive: O_EXCL lock file as compare-and-swap, PID + 300s TTL so a crashed holder cannot wedge, optimistic hash-on-read/re-hash-before-write, `os.replace` for atomicity. Its docstring cites a REAL observed loss — `ops_audit_sta…

### the_loop_could_not_tell_whether_it_was_emptying_anything.md
*"2026-08-06 — trajectory() added so 15 amnesiac loop sessions can see whether surfaces are emptying; building it caught two of my own published numbers being wrong (dark tools 6 vs 15, and 13-of-13 lanes \"silent\" from a schema mismatch)"*
Chairman ruling 2026-08-06: *"you may go through 15 loops at least before I'm back — make sure each loop improves things; we don't want the last loop looking like the first."* The apparatus could not answer that. `--status` printed the right sentence — *the number to watch is whether the evidence surfaces are emptying* — **and then computed nothing.**  **The fix is the placement, not the maths.** …

### the_loop_measured_its_product_and_never_itself.md
*"2026-08-04 — 11 mechanical vs 1 permission roadblock; the day's headline work went at the named blocker, not the real one. friction.py / rule_echo.py / loop_health.py"*
**MEASURED, not asserted: 11 MECHANICAL roadblocks against 1 PERMISSION roadblock** — in the session whose entire headline work was aimed at the permission gate. Basis: `friction_ledger.jsonl`, read by `loop_health.py` — mechanical 11 events / 82 min, operator 2 / 10 min, permission 1 / 0 min.  **Why it went unnoticed for so long: every instrument measures the PRODUCT** — services promoted, contra…

### the_loops_own_first_command_produced_its_top_hazard.md
*"FU-294/cycle-0028 — 7 of 17 mcp-timeout-orphan rows were `improve_loop.py --select`, the loop's own mandatory first command; friction's slow-tool roster omitted it. Fixed by self-detaching the tool, not by documenting the remedy."*
`mcp-timeout-orphan` was ranked #1 by the selector and **survived three cycles aimed straight at it** (0015, 0021, 0026). Each worked the CATALOGUE — the hazard entry, the `fix` string, the constructor — and the catalogue was never what failed. All three remedies existed, were correct, and required every caller to remember them.  **The find, 2026-08-09: classify the rows by MECHANISM and 7 of 17 a…

### the_loops_select_self_detaches_but_its_verify_does_not.md
*"improve_loop --select detaches itself, --verify runs foreground and exceeds the MCP cut by construction; two prior cycles are stranded in SELECTED"*
cycle-0031, 2026-08-09. `improve_loop.py --select` self-detaches (`friction.detached` + a poll tag). **`--verify` does NOT.** Its predicate is routinely a full 88-tool `dark_tools.py` census (~1.5 min per target), so a foreground `--verify` **exceeds the ~90s MCP cut BY CONSTRUCTION**. Mine was cut; the orphan survived, finished its work, and `improve_loop.json` was never written — cycle-0031 sat …

### the_memory_mcp_blocked_its_own_handshake.md
*"2026-08-31 memory-mcp ran a 30-140s synchronous reindex BEFORE the MCP initialize, so clients timed out and silently dropped the server; fixed via background thread + freshness guard; rot_detector.py now audits both memory systems"*
2026-08-31: The Tower memory MCP (`C:\Users\robin\.claude\tools\memory-mcp\server.py`) was **absent from sessions not because it crashed but because it blocked its own handshake**: `main()` ran `reindex()` synchronously before `build_server().run()`. With hourly transcript churn (~350 files, 97–143s) or a locked db (30s busy_timeout — probe measured 35s and caught a live "database is locked"), the…

### the_monitor_was_the_broken_thing.md
*"verify_candidate.ps1 called an EMPTY orphan dir FATAL and could gate nothing; git accepts an empty dir. #2173/FU-130."*
2026-07-28: prod-drift-sentinel could not gate ANY deploy candidate. `Reset-DisposableWorktree` found `D:\zo\_prod_dryrun` present with **zero entries**, retried 5× printing *"left 0 file(s) — handle likely still closing"*, then `Die()`d with "a process is holding a file open".  Both halves wrong. **MEASURED: `git worktree add --detach` succeeds into an existing EMPTY directory** (exit 0, full che…

### the_named_test_did_not_exist.md
*"the SKILL ordered every harness change to run db_backups/_verify_fixes.py; the file was gone from the box while its 07-27 pass log survived, and no run reported it missing"*
The `mcplookup-nightly-db-backup` SKILL tells every run: after ANY change under `db_backups/`, run `_verify_fixes.py (6, LIVE ...)`. On 2026-08-04 **that file did not exist anywhere on the box** (recursive search of `D:\zo` and `C:\Users\robin\OneDrive\Documents`). Its 07-27 output log survived at `db_backups/logs/verify_fixes.log` showing 6/6 PASS — so the test was real and ran once; the **script…

### the_one_class_that_costs_money.md
*51% of the FOLLOWUPS ledger (75/146) is ONE failure class — the artifact you inspected is not the artifact that runs. Guardrails failed because none had ever been seen RED.*
Chairman, 2026-07-28: *"we have wasted plenty of money with guardrails — it didn't stop failed training jobs, a Potemkin e2e builder ladder and other losses. If we can fix the repeated class of problems first through adaptive tasks with autop then we can design a new harness for idempotent."*  Measured it rather than agreeing rhetorically: of **146** FU entries, **75 (51%)** match one class.  > **…

### the_only_exit_from_the_census_was_keyed_on_a_name_that_is_not_unique.md
*"2026-08-05 cycle-0005 — the largest dark tool was dark because wiring it is FORBIDDEN by a CofC ruling, a third category the census could not express; and EXPECTED_DARK, its only exit, was keyed on a basename that collides"*
**2026-08-05, improvement-loop cycle-0005 — the first cycle this engine has ever VERIFIED (predicate RED(1)→GREEN(0)), on the exact target cycles 0001–0004 could not close.**  `tools/anchor_self_refill.py` (34,446B) was the top-ranked dark candidate four cycles running. `dark_tools` offered two branches — **wire it**, or **explain it** — and preferred wiring. **Wiring it would have been the defect…

### the_probe_greps_a_word_the_emitter_stopped_saying.md
*"FU-196 — the FU-031 degradation probe matched \"self-test FAILED\" while goose_runner emitted \"self-test RED\" for 28h+, so it published 0% degradation / 100% pass over 320 blocking failures"*
2026-07-30. `tools/builder_selftest_integrity_report.py` printed `executed: 21 (pass 21 / failed-blocking 0) | tier0-degraded: 0`, `DEGRADATION RATE: 0%`, `pass-rate 100%`. All four numbers were void.  The probe matches two regexes: `self-test FAILED` and `import/env failure -- degrading to Tier-0`. The live emitter, `goose_runner.py:1062`, writes `self-test RED (<reason>) -- blocking completion`.…

### the_prod_schema_has_two_owners.md
*"prod Postgres splits table ownership 8/11 between `mcplookup` (superuser) and `mcplookup_app`; alembic connects as the latter, which owns neither `users` nor `orgs`, so the schema-evolution path for the core product has been dead since the second owner appeared"*
Measured inside the running Fly machine 2026-08-02T18:18Z with the role alembic actually connects as (R1 — not read off a config file): `current_user` = `session_user` = **`mcplookup_app`**, `rolsuper=false`.  Ownership in `public` splits **8 / 11**:  - **`mcplookup`** (a SUPERUSER) owns `users`, `orgs`, `api_keys`, `api_usage`, `app_stats`,   `mcp_llm_axis_scores`, `mcp_score_disputes`, `mcp_serv…

### the_promoter_wall_changed_owner_on_2026_08_02.md
*"FU-217/FU-220 resolved; the promoter's liveness contract executed for the first time (266 runs), 10 services are promote-eligible, and the remaining wall is our own generated code."*
Measured 2026-08-02T14:41:25Z, runtime `5c9ebdaa`, `promote_staged_to_active.py` in OBSERVE mode.  - **333 candidates · 10 PROMOTE · 323 HOLD** (was 300/0/300 on 2026-08-01 14:32Z). - **The `COPY` hold bucket went 267 → 0** — FU-217's `COPY services/active` landed 2026-08-01 as `931bef3a` (PR #2626). FU-220 (a `NameError` in `services/staged/__init__.py`) fixed in the same PR. - **The liveness con…

### the_promoter_wall_is_a_dockerfile_line_that_does_not_exist.md
*84% of promoter HOLDs need a services/ COPY that the Dockerfile has never had — the last agent-ownable lever on T2 closed on 2026-08-01*
FU-217, measured 2026-08-01T02:09Z on runtime `f570f1ba`. Promoter OBSERVE: **258 candidates, 0 PROMOTE, 258 HOLD.** The COPY bucket is **225 = 218 tracked / 7 untracked**.  Two facts that end the previous framing:  1. **The tracked/untracked split INVERTED.** On 7/30 it was 134 tracked / 22 untracked, and the 22 were "ours, needs only a PR". The builder committed ~88 staged services in 36h, so th…

### the_promotion_gate_reads_the_worktree_the_image_ships_git.md
*"promote_staged_to_active.py evaluates the working tree, but the image is built from a clean checkout — 252 of 366 candidates have a contract.py that is not in git, and all 10 promote-eligible services have untracked files."*
BASIS 2026-08-03: `git ls-tree -r origin/main services/staged/` @`26e46c31` vs the worktree.  Of **366** promoter candidates: **56 fully tracked · 281 partially tracked · 29 wholly untracked**. **252 of 366 have a `contract.py` that exists on disk but not in git.**  **ALL TEN promote-eligible services have untracked files.** `registry_source_freshness_dashboard` is **1 of 5** tracked; the rest are…

### the_ranker_and_its_predicate_counted_different_populations.md
*"2026-08-12, cycle-0040/FU-334 — improve_loop ranked recurring_friction from an UNWINDOWED ledger walk while the predicate it attached graded --days 7, and asserted 'still recurring' as a literal constant. It selected a family cured 5 days earlier, whose predicate would have gone GREEN BY AGING OUT on 08-14. FIXED: window+key aligned to row_key, quiet_days published, DRAINING label on both surfaces."*
**A HEADLINE AND ITS PREDICATE ARE TWO INSTRUMENTS. ASK WHETHER THEY COUNT THE SAME POPULATION.** `improve_loop.candidates()` built the `recurring_friction` item from a walk of `friction_ledger.jsonl` with **no time filter at all**, keyed `sig or signature(what) or raw`. The predicate it attached graded **`--days 7`**, keyed `friction.row_key()` — the function whose docstring exists to stop exactl…

### the_reachability_ratchet_baseline_went_stale_and_became_a_level_gate.md
*FU-367 — capmap-check fails 25/45 open PRs with one identical verdict because the orphan baseline is pinned at 277 while the live census is 335*
**2026-09-01.** `capmap-check` fails 25 of the newest 45 open PRs with the *same* verdict every time: `REGRESSION (orphans=335 baseline=277 delta=+58 mode=enforce)`, from `tools/reachability_ratchet.py --enforce`.  **Why:** the ratchet's contract is to gate the **DERIVATIVE** — silent orphan growth *from the PR under test*. `pr-gates.yml` states it: "the baseline is pinned at 276 (the level, froze…

### the_reaper_asserts_a_cause_it_never_measured.md
*"3 zombie snapshot runs (24, 26, 54); the reap marker says 'presumed dead (OOM/restart)' and nothing has ever confirmed it. FU-163."*
`_reap_zombies()` in `cadence_admin_api.py` correctly fails any cadence run stuck at `status='running'` past `CADENCE_ZOMBIE_HOURS` (6). Its marker reads `"zombie: still 'running' after 6h; worker presumed dead (OOM/restart); reaped"`.  **`presumed` is doing real work in that sentence.** Three runs have died this way — 24, 26, and 54 (2026-07-28, confirmed dead at 25h01m) — and OOM has never been …

### the_recurrence_counter_was_keyed_on_free_text.md
*"loop_health read the fleet's worst hazard as x3 when it was x12 across 8 lanes, because `repeats` was keyed on a free-text sentence — and all twelve rows already named the canonical family in their own prose"*
2026-08-06, improvement-loop **cycle-0008** (FU-264). `loop_health.stalls()` keyed `repeats` on `(class, what)` where `what` is a **free-text sentence**, so two lanes hitting the identical hazard collided in the counter only if they typed byte-identical prose.  **MEASURED:** 68 rows in the 14d window; **twelve** are `ps-command-dollar`, filed by **EIGHT different lanes**, latest `2026-08-06T05:01:…

### the_refresh_cycle_stretched_to_14_weeks_while_the_sla_stayed_green.md
*"refresh_cap 20k/week against 280,811 scored servers is a ~14-week full-refresh cycle, not the manifest's ~4; newest_scored_at hides this completely because it is a max()"*
`jobs/registry_rescore_weekly.json` claimed the 20k/week refresh cohort meant "every server refreshed <= ~4 weeks". Measured 2026-08-04 from `GET https://mcprisky.io/freshness`: **280,811 scored / 20,000 per week = ~14 weeks.** The ~4-week figure was calibrated when the moat held ~80k servers and outlived its unit.  Corroborated by a second field on the same payload rather than asserted: `oldest_s…

### the_refresh_half_of_the_cohort_cannot_move_the_corpus_floor.md
*oldest_scored_at frozen since 07-19 across 385k refresh slots; the SLA reads a max() and never looks at the floor. FU-361 open.*
**`oldest_scored_at` = `2026-06-24T15:46:24.527410` on EVERY landed delta wave since `20260719-003024`** — identical to the microsecond, across **385,000 cumulative refresh-server slots**, *including* the 140k and 120k waves that changed 97% and 99.99% of their cohorts. So this is not a symptom of the zero-yield regime; **even a perfectly working refresh half never moves the floor.**  Likely mecha…

### the_refresh_half_produced_one_change_in_sixty_thousand_slots.md
*"Delta refresh cohort went 76-99% changed to 0/0/1 per 20k after 2026-07-30; three landed waves, four weeks, nobody read the number. Instrument shipped PR #4365."*
Replay every `state.json` in `D:\zo\runs\weekly_rescore` through `refresh_yield()`, max(changed) over all 7 axes:  ``` 20260726-014732   19,999 / 20,000    99.99 % 20260727-024623  119,992 / 120,000   99.99 % 20260727-105859  136,784 / 140,000   97.70 % 20260730-001738        0 / 20,000     0.00 %   <-- regime change 20260804-060703        0 / 20,000     0.00 % 20260831-033413        1 / 20,000   …

### the_repair_an_error_message_invited_was_the_regression.md
*"A jammed revert said \"the patch never landed, or its SHA was never recorded\"; writing the SHA would have git-reverted a correct patch into prod. FU-363."*
2026-09-01, found by `mcplookup-nightly-db-backup`. Decision `bar-csv-machine-writer-must-not-erase-graded-rows` sat in `REVERT_FAILED` for 7h while **eight lanes** retried its revert, each printing *"a broken change is LIVE"*. Nothing was broken: the patch landed as `60d85810` (PR #4327) at 2026-08-31T17:21:17Z, **three minutes before the lane recorded ACTED**, and both predicates are green.  `ba…

### the_run_that_fired_prod_v66.md
*"2026-08-02 — prod-drift-sentinel fired prod itself for the first time (v65→v66, sha d5cb1d0f), taking drift from 452 commits to 0. The exemplar for what a complete FIRE_ON_GREEN precondition set looks like when actually measured."*
**First lane-fired prod release.** v65 → **v66**, sha `d5cb1d0f`, 2026-08-02T00:54:53Z, image `deployment-01KYZZEG336RAKYQQZSWHA9C40`. Drift **452 → 0**. Cause of the 20-stage delay: [[a_permission_restated_outside_the_grant_file_cannot_be_revoked]].  The five `authority.json` preconditions, each resolved from a runtime surface rather than a repo path (R1):  1. **8/8 gates PASS** on the sha actual…

### the_safe_constructor_handed_the_child_a_cp1252_stdout.md
*"friction.py's --spawn/run()/pyrun() -- the constructor the fleet is told to use -- gave the child a cp1252 stdout, so a probe that did everything right still died with UnicodeEncodeError on the U+2192 in every tower corpus; fixed 2026-08-08 cycle-0023 with PYTHONIOENCODING=utf-8. A HALF-cured hazard reads to the paying lane as UNCURED. RECURRED 2026-09-01 on friction.py's OWN stdout (--grep), cured at main() entry -- see the 2026-09-01 update at the bottom."*
2026-08-08, improvement-loop cycle-0023. Selected `inline-interpreter-source` (4 bites / 7d, all `prod-drift-sentinel`). Hunting the path, I hit the hazard **inside the tool built to prevent it**: a probe written to a `.py` FILE and launched BY PATH through `friction.py --spawn` — the exact safe form six lanes converged on and FU-264 turned into `pyrun()` — died `child_rc=1`, `'charmap' codec can'…

### the_sanctioned_ledger_writer_flips_line_endings_and_fails_open.md
*"_tools/fu_lock.ledger_txn rewrote all 2,969 CRLF endings in FOLLOWUPS.md to LF, and when it could not remove its own stale lock it let the caller proceed UNLOCKED"*
Two defects in `_tools/fu_lock.py`, both found 2026-07-30, both in the writer every task is told to use for `FOLLOWUPS.md`. Ledgered as **FU-201**.  **1. It flips the whole file's line endings.** `ledger_txn()` opens with `open(path, encoding="utf-8")` — text mode, universal-newlines ON — so CRLF is translated to LF on read, then committed back via `"\n".join(txn.lines)`. The ledger is a Windows-s…

### the_sanctioned_writer_had_no_test.md
*"fu_ledger.append_log split a wrapped log bullet and re-parented its prose — the designated safe path, 178 entries deep, with no test file at all"*
`tools/fu/fu_ledger.py::append_log` is the writer every lane is told to use INSTEAD of a hand text-replace, on the stated grounds that a hand edit is how a log line becomes invisible to the linter meant to police it (see [[my_own_ledger_entries_were_invisible_to_the_linter]]). It found the end of a `- log:` block with `while lines[pos].startswith("  - ")` — bullet HEAD lines only. Log entries rout…

### the_sanctioned_writer_has_no_cli_and_exits_zero.md
*"`python tools/fu/fu_ledger.py --append-log N --message ...` exits 0 and writes nothing — it is a library with no __main__; use _tools/fu_append_log.py"*
`tools/fu/fu_ledger.py` is the writer every charter calls **SANCTIONED**, and it is a **library**: no `__main__`, no argparse, `--help` prints nothing. Running  ``` python tools/fu/fu_ledger.py --append-log 235 --message "..." ```  imports the module, defines `parse` / `append_log` / `insert_key`, **discards every argument, and exits 0.** Measured 2026-08-03: rc=0, then re-reading FOLLOWUPS.md gav…

### the_scheduler_dormancy_is_a_recurring_class_and_resume_day_censuses_lie.md
*"Tower Claude scheduler has gone dormant fleet-wide twice (08-13..08-22, 08-24..08-31); on resume it burns the whole missed backlog in ~45 min, and lane_start silence readings taken during that burst are the rewrite, not the fleet"*
The tower Claude scheduler went dormant fleet-wide 2026-08-13→08-22 (230.44h backup gap, FU-208-acked) and AGAIN 2026-08-24→08-31 (164.8h gap, logged as a dated bullet under FU-208 on 2026-08-31). Root cause UNDIAGNOSED after two occurrences — chairman informed 08-31 while present.  **Why:** on resume the scheduler fires every missed lane in sequence (08-31: all lastRunAt stamps 03:13–04:00Z). Dur…

### the_scratchpad_hazard_bit_three_lanes_and_the_census_never_saw_it.md
*"Copying out of the Cowork scratchpad returns rc=0 and writes 0 bytes; 3 lanes hit it, all read as UNKEYED singletons, so RECURRING never fired. Remedy = route via the workspace-sandbox mounts."*
`Copy-Item` from the Cowork scratchpad (`C:\Users\...\local-agent-mode-sessions\...\outputs`) to `D:\zo\...` **returns rc=0 and produces a 0-BYTE file.** The tower shell cannot resolve that path, so the copy succeeds and writes nothing — indistinguishable from a written file until you `stat` it.  **The remedy, which the fleet did not have written down:** route the bytes through the workspace-sandb…

### the_shape_of_the_output_tells_you_if_you_ran_the_right_instrument.md
*"A clean-clone spine check returned a false REGRESSION because it was the wrong binary; the tell was that the baseline string carried a \"[known]\" annotation the new output lacked."*
On 2026-08-02 the spine check in the FRESH CLONE — the designated negative control — came back `verdict: BROKEN (services=32 broken=5)` rc=1. The established baseline was `verdict: CLEAN (services=31 broken=6[known])` rc=0. Publishing that would have been a headline regression in the one tree that is supposed to be clean.  It was not a regression. I had run `tools/spine_manifest.py`; the instrumen…

### the_silent_lane_detector_enumerated_lanes_from_the_receipts.md
*FU-286 — lane_start could only call a lane silent if it had already checked in; 4 of 17 ENABLED lanes were invisible by construction. Fixed via lane_roster.json + per-cadence windows*
`lane_start.check_in()` iterated `doc["lanes"]` — **the receipts file**. A lane that never called `lane_start` had no age, so no value could put it in the silent list. Measured 2026-08-08T12:06Z: **13 in `lane_receipts.json`, 17 ENABLED in the scheduler.** Invisible: `mcplookup-nightly-db-backup`, `cadence-jobs-daily-trigger`, `moat-rescore-weekly`, `goose-shadow-research`. It printed "1 sibling l…

### the_skip_permissions_toggle_reverts_but_nothing_is_blocked.md
*"the scheduled-task 'skip permissions' toggle displays as 'manual' again, but 17/17 enabled tasks fired on schedule and permission stalls are 0 in 24h — display-only, and not fixable from our side"*
Asked twice on 2026-08-06 (re-checked 20:10Z, again ~20:4xZ). **The toggle reverting is display-only. Nothing is being blocked.** Do not re-investigate a third time without new evidence — check the two controls below instead.  **The symptom conflates two questions, and only one of them costs anything:**  1. *Does the toggle's displayed state revert?* — apparently yes. 2. *Are runs actually blocked…

### the_split_was_built_on_the_progress_number_not_the_alarm_number.md
*"fu_drift_diff split CLEARED three ways but summed NEW into one figure, so 1 real defect hid inside 45; guard the number that raises the alarm, not just the one that claims progress"*
2026-08-05, graphify-kl-daily-refresh. `fu_drift_diff.py` carried a long docstring — added 2026-08-02 — explaining that **CLEARED IS NOT ONE THING** and splitting it into `resolved_in_graph` / `cleared_by_closure` / `deanchored`, with the instruction that these "must never be summed". `NEW` stayed a single integer.  Today it printed **`+45 new`**. The truth underneath: **44 out_of_scope** (tower-l…

### the_staged_one_click_reinvented_a_tested_runbook.md
*"prod_deploy_staged.md hand-rolled a worse copy of ops/host/deploy_prod.ps1 for 12 stages, and its acceptance step asserted nothing (FU-156, PR #2184)"*
`ops/host/deploy_prod.ps1` has encoded the full prod release runbook since the v63→v64 ship. For **twelve consecutive stages** `prod_deploy_staged.md` shipped the chairman a hand-rolled duplicate instead, and every difference was a dropped safeguard:  - `--build-arg GIT_SHA` only, no `BUILD_TIME` → `/version.built_at` stays `unknown` forever - rollback command without `--yes` → would have **prompt…

### the_status_endpoint_answers_absent_for_every_required_check.md
*"gh api commits/<sha>/status reports all 7 required contexts ABSENT for a sha whose /check-runs says all 7 SUCCESS — same sha, no error either way; use tools/sha_green.py"*
The 7 required contexts (capmap-check, static-analysis, smoke-ladder, frontend, pytest, no-hollow, schema-prm) are GitHub **Actions check-runs**, not legacy commit **statuses**. So:  - `gh api repos/O/R/commits/<sha>/status`     -> empty `statuses[]`, every context ABSENT - `gh api repos/O/R/commits/<sha>/check-runs` -> all 7 SUCCESS  Same sha, same second, **neither errors**. A gate built on `/st…

### the_strict_spine_gate_grades_the_wrong_tree.md
*"generate_spine.py --check --strict reads the FILESYSTEM, so on the tower it is permanently red from untracked builder residue and says nothing about the deploy — same SHA, clean checkout, rc=0"*
`tools/generate_spine.py --check --strict` resolves services from `ROOT/services/active` on the **filesystem** (presence of `service.toml` == registration). The tower working tree is not the repo: on 2026-08-01 it carried **19 untracked service dirs that exist in no commit**, 17 of them a bare `router.py` with no `service.toml`. So the gate was red for reasons that had nothing to do with the deplo…

### the_supersedes_prose_array_was_never_read_by_anything.md
*The real authority-clash class is not two lanes owning one resource -- worktrees already isolate that. It is a retired instruction still live in a SKILL while the grant that retired it goes unread. authority.json.supersedes_prose listed the retired sentences VERBATIM and zero code referenced it. authority.py --retired-prose now greps the quoted fragment; suppression must be LOCAL and case-insensitive.*
The chairman's standing complaint is that lanes "fight over authority". Measured, none of the observed fights is two lanes contending for one resource — `lane_worktree.py` already isolates that ([[lane_isolation_and_shadow_halt_shipped]]). Every real clash is one shape: **two sources disagree and the lane obeys the one it read last.**  prod-drift-sentinel's SKILL said "the chairman fires the push"…

### the_tool_built_to_prevent_a_hazard_reproduced_it_inside_itself.md
*"friction.detached() never launched anything on this box, from any caller, and reported success while doing it — four stacked silent defects, all fixed 2026-08-04"*
`friction.detached()` — the constructor every lane's prompt names for work over ~3 minutes — **had never launched a single process.** Found 2026-08-04 by `prod-drift-sentinel` when its own `verify_candidate` launch produced no output. Four defects, stacked, each silent alone:  1. **list vs str.** Typed for `str`; the natural call passes a list. `json.dumps(list)` wrote a    JSON ARRAY into the wra…

### the_tools_fu_ledger_copy_is_a_diverged_shadow_not_a_stale_one.md
*"_tools\\fu_ledger.py and tools\\fu\\fu_ledger.py have DIVERGED APIs; sys.path order silently picks the writer, and on 2026-08-23 the wrong one glued a ledger bullet onto FU-035's resolution key"*
`D:\zo\Zocomputer Agents\_tools\fu_ledger.py` and the repo copy `tools\fu\fu_ledger.py` are NOT supersets of each other: `_tools` adds `DuplicateFU`/`by_id`/`next_num`; the repo copy adds `line_terminator`/`_is_wrapped_log_line`. On 2026-08-23 a script did `sys.path.insert(0, lane)` then `sys.path.insert(0, _tools)` — the SECOND insert wins — imported the pre-fix `_tools` writer while its author h…

### the_tower_clock_was_a_day_behind_and_corrected_itself_mid_run.md
*"CORRECTED 2026-08-06 — there was NO clock jump. FU-266: an agent session was SUSPENDED ~23h and resumed, so pre-suspension measurements were silently a day stale. Filename kept only so the retracted claim stays findable."*
**The clock was never wrong. This file's own name is the retracted claim.** Kept under the old slug so anyone who saw the first version lands here.  **2026-08-06, vast-jobs-daily-audit (FU-266).** Tools early in one session stamped `2026-08-05T11:36–11:38Z`; `Get-Date` later in the *same* session returned `2026-08-06T11:09Z`. I filed that as a **+23h33m clock jump**. It was not. **The session was …

### the_tower_survives_a_reboot_but_the_run_key_does_not.md
*"2026-08-02: the fleet DOES auto-recover from a reboot -- MSIX ClaudeStartup State=2 is the mechanism, NOT the HKCU Run key, which a Store auto-update left pointing at Claude_1.8089.1.0 while 1.24012.9.0 is installed. Windows Update pause had been EXPIRED since 05-24; paused to 08-31."*
All 15 enabled lanes run inside Claude Desktop on the tower, so "does the tower come back by itself" is a single point of failure for the entire fleet. Checked before the 08-30 absence, because the tower cannot be rebooted by hand while he is away.  **Windows Update was live.** `PauseUpdatesExpiryTime` had read `2026-05-24T02:37:00Z` — expired for ten weeks — so a forced reboot could land on any d…

### the_triage_check_is_cancelled_by_design_and_not_required.md
*"Every open auto/build PR reads red because pr-triage cancels its own in-flight sweep — but `triage` is NOT a required check, so those PRs are UNSTABLE, not blocked. Stop re-deriving this."*
2026-07-30. ~30 open `auto/build/*` PRs all show `mergeStateStatus: UNSTABLE` with a red `triage` check. This is **not CI failure and not a blocker.**  `.github/workflows/pr-triage.yml` sets `concurrency: {group: pr-triage, cancel-in-progress: true}` *and* triggers on `pull_request_target`. So a burst of build PRs makes each new PR cancel the in-flight sweep, and every PR inherits a `CANCELLED` tr…

### the_worktree_claimed_14_promotable_the_image_could_carry_4.md
*"2026-08-06 PR #2929 — the promoter grades the working tree, prod ships git; run it on a clean clone to get the shippable number"*
**2026-08-06.** `promote_staged_to_active.py` computes eligibility from the **working tree** on the zo box. The prod image ships **git**. Those are different populations and the gap is the whole story:  | tree | candidates | PROMOTE | |---|---|---| | zo box worktree (08-05) | 441 | **14** (honest 12, 2 hollow) | | clean `origin/main` clone, tower-side (08-06 12:10Z) | 425 | **4** |  Firing `--enfo…

### the_zo_email_reply_loop_is_one_way_while_credits_are_capped.md
*Zo still SENDS sentinel email but cannot PROCESS replies while AI usage is capped — chairman decisions sent by email since 2026-08-01 were never received*
Confirmed 2026-08-03. Robin's Zo AI usage has been capped since **at least 2026-07-27**. Every reply he sent to `robinc@zo.computer` bounced within ~40s with *"Add credits or bring your own provider to continue."*  | Decision sent | When | Outcome | |---|---|---| | "A" — Dockerfile `COPY services` (FU-217) | 08-01 03:09Z | never received | | "D" — FU-235 option (D) | 08-03 12:58Z | never received …

### three_builders.md
*"CORRECTED. Two-tier build architecture: Goose-Architect (MiniMax via shim) reasons + writes specs; ZoBuilder (Ollama) is the fast-builder it delegates codegen to via builder_mcp. Plus directive_generator upstream feeding the queue."*
CORRECTED 2026-05-25 — earlier framing called `zo_sentinel_builder` "legacy". It is NOT legacy. It is the fast-builder tier that Goose-Architect delegates to via the `delegate_to_builder` MCP tool.  **Two-tier build, one feeder:**  1. **Goose-Architect** (driven by `goose_runner.py` → `goose_recipes/architect.yaml` v1.1)    - Recipe title literally "Zo Sentinel Architect"    - PRIME DIRECTIVE: "NE…

### three_link_namespaces_one_graph.md
*"2026-07-29 — graphify links join the FU ledger to memory, but [[...]] spans THREE namespaces: [[FU-NNN]] (intra-ledger), [[snake_case]] (spaces memory, 266 nodes), [[hyphen-ated]] (MCP memory, 197 incl. one DERIVED node per FU). A checker that knows one namespace reports the other two as dangling. Audit: _tools/kl_link_audit.py."*
Graphify links exist to bring the ledger and memory into one graph. Every edge is a hand-written `[[wiki-link]]`, so every edge can rot silently — the entry still reads fine, it just stops being reachable from the other store.  **Three namespaces, one syntax:**  | link form | resolves against | count | |---|---|---| | `[[FU-054]]` | FU headings in `FOLLOWUPS.md` | 175 entries | | `[[scored_servers…

### threshold_outlives_the_unit_it_measured.md
*"MIN_SOLID_ADDITIONS=12 silently made every service unpromotable once the atomic unit changed from FILE to SERVICE — 50 staged dirs, 13 manifests. Fixed PR #2060."*
`tools/pr_triage.py` carried `MIN_SOLID_ADDITIONS = 12`. It was **correct** when the atomic unit was the FILE and a tiny diff honestly meant a hollow stub. The unit became the SERVICE; the threshold did not change; and it **inverted**.  Under the service unit one `build_service` directive fans out to five PRs, and the **smallest of the five is the load-bearing one**:  - `tools/promote_staged_to_ac…

### tower_is_never_current_is_a_parked_branch.md
*"'The tower is never current' has a cause: D:\\zo\\zo-sentinel\\zo-sentinel is PARKED on fix/architect-salvage-timeout-renders, 88 behind. _runbook is a worktree of it and is exactly at main."*
Chairman, 2026-07-29: *"tower is never current I don't know why."* Measured it. It is not a sync failure.  ``` D:\zo\zo-sentinel\zo-sentinel   fix/architect-salvage-timeout-renders  e2744d3e  behind=88  ahead=1  dirty=3 D:\zo\_runbook                  (detached worktree)                    14552387  behind=0   ahead=0  dirty=0 D:\zo\_fire_main                (detached)                             …

### tower_postgres_standing.md
*"The app-data Postgres now STANDING on the tower (no-admin portable PG 16.6): connection, how to start it, the schema (PR #566 migrations), and that writes are verified. This is the new home for registry + SFT scores (off the box's single-writer DuckDB); builder keeps DuckDB. Fly-portable."*
**Stood up 2026-06-24 on the tower (rczompsentinel, Windows).** The tower has **NO admin rights** (can't winget-install a PG service), so it's the **EnterpriseDB PORTABLE PG 16.6 binaries** (no-admin, user-space): - Binaries: `D:\zo\pg16\pgsql\bin` (psql/pg_ctl/initdb/postgres). Data dir: `D:\zo\pg16\data`. Port **5432**. - DB **`zo_sentinel`**, app role **`zo`**, superuser `postgres`. **Password …

### treewalk_2026_07_03_findings.md
*"Live click-through — Perspectives WORKS (facets/saved/drill-down) but 10-20s UI freezes, global-not-conditional facet counts, trust-diff silent no-op; recursive-perspectives design doc in workspace"*
2026-07-03 admin click-through of mcprisky.io. Full findings + design: D:\zo\Zocomputer Agents\TREEWALK_2026-07-03_findings_and_pathways.md.  VERIFIED WORKING: dashboard counts; explore wildcard search; perspectives facets + conjunctive drill-down + 5 saved perspectives ("High & Critical risk"=21,812); ask UI grounded w/ why-expanders; /scan honest UNKNOWN + kill-switch message; server detail "raw…

### treewalk_fixes_shipped_2026_07_03.md
*"Treewalk findings RESOLVED same-day: PRs #1221-#1224 (perspectives v1.2 SQL perf + conditional facets + URLs, brand, dispute UI, CI smoke); DB hit disk-full readonly -> volume 1->3GB; freeze root cause was SERVER-side ORM materialization"*
2026-07-03 PM session (CofC 3+FATHER ruled; full council notes in transcript):  **Freeze root cause (treewalk #1) was SERVER-side, not client**: query_perspective_servers materialized ALL matching ORM rows (21k-80k/request), Python pagination/counts; diff used page_size=1e9 AND wrote PerspectiveEvent rows on public GETs.  **Shipped (all merged + deployed):** - #1221 perspectives v1.2: SQL count/LI…

### treewalk_polish_and_council_roadmap.md
*"2026-07-02 late — treewalk polish deployed (nav, real dashboard API, ad-hoc perspectives, facet perf, named citations) PRs"*
2026-07-02 (late): **Treewalk → polish → council roadmap, all deployed.**  **Treewalk findings fixed (PR #1177, live on mcprisky.io):** (1) Perspectives/Ask added to the SPA nav; (2) dashboard was stuck on "loading" because `/api/dashboard/summary` was a HOLLOW factory artifact importing nonexistent modules AND missing from the Dockerfile — replaced with a real implementation; (3) /api/facets ~25s…

### treewalk_smoke_dead_undeclared_requests.md
*"treewalk-smoke had NEVER passed on any PR branch — root cause was `requests` undeclared in app/requirements.txt; fixed in #1814 on 2026-07-25."*
`treewalk-smoke` (ui-smoke.yml) was dead-red on EVERY PR branch since it was introduced (#1224) — including keystone #1786, where it was the lone red.  ROOT CAUSE: `verdict_breakdown_api.py:23` imports `requests` at module scope (webhook POST at line 253). #1798 wired that module into the app import chain, but `requests` was never added to `app/requirements.txt`. ui-smoke installs only `app/requir…

### triage_condemned_prs_for_its_own_cancel.md
*"pr-triage read its OWN cancelled check as gate=failure and stalled 58 fully-green PRs; a self-reinforcing loop, fixed by self-exclusion + CANCELLED-as-pending. FU-141/#2176."*
2026-07-28. 121 open PRs, ~58 `build:`/`scaffold:` PRs fully MERGEABLE with every real gate green, nothing merged 02:09Z→12:00Z. The blocker was **the triage bot condemning PRs for its own concurrency cancellation**:  1. `pr-triage` runs `cancel-in-progress: true` — each new build PR kills the sweep,    leaving a `CANCELLED` **`triage`** check on PRs the sweep already touched. 2. `CANCELLED` was i…

### twentyk_goal_and_sft_scoring_gap.md
*"20K-MCP goal status (1832/20k, discovery COLLAPSED) + the root cause the SFT scorer stalled at 629 (the pipeline never scored the registry, only the eval set) + the nightly-scoring fix + Fly secrets/DB-auth model. 2026-06-24."*
**20K-MCP goal status (2026-06-24, live DB):** `mcp_server_registry` = **1,832 / 20,000 (~9%)**; signal-scored 1,531; **LLM-axis-scored only 629**; risk_register 200. **Discovery has COLLAPSED:** new servers/week 757 (Jun 1) -> 194 (Jun 8) -> 75 (Jun 15) -> 27 (Jun 22). At this pace 20K is 4+ years out — the feeder **exhausted its current registry sources**. To reactivate growth, NEW discovery sou…

### two_questions_sharing_one_clock_turn_a_race_into_a_terminal_verdict.md
*"2026-08-11 cycle-0038 (FU-330) — friction.self_detach() used the CALLER's --detach-wait as the deadline for BOTH 'has the work finished?' and 'did the wrapper ever start?'. The .started sentinel is written asynchronously, so --detach-wait 0 returned rc=2 NEVER LAUNCHED for a completely healthy launch: an orphan manufactured by the anti-orphan tool. Fix: LAUNCH_GRACE_S, a separate clock owned by the launcher."*
`self_detach()` distinguishes **STILL RUNNING (3)** from **NEVER LAUNCHED (2)** by whether the launcher wrote its `.started` sentinel. It used the caller's `wait` as the deadline for both. But `.started` is written **asynchronously**, so with `--detach-wait 0` the parent asked *"did it ever start?"* ~200ms before the answer could exist and returned **rc=2 for a launch that was completely healthy**…

### two_terminator_defects_masked_each_other_in_the_ledger_writer.md
*The ledger writer appended bare-LF lines and its idempotence guard was terminator-sensitive; fixing the first defect activated the second and produced a duplicate.*
`_tools/deploy_ledger_*.py` (the pattern every lane copied from `deploy_ledger_20260810b.py`) carried **two terminator defects that cancelled each other**, so both were invisible until one was fixed.  1. **Append is terminator-lossy.** It reads with `newline=""` and splits on    `"\n"`, so surviving lines keep their trailing `\r` — but a freshly composed    bullet has none, and lands as **bare LF …

### unevaluated_was_being_summed_into_trusted.md
*"The moat trust headline had two buckets where the gate emits three — INSUFFICIENT was counted as TRUSTED, hiding 1,200 servers (FU-175, 2026-07-29)"*
`_fu108/cohort_audit_served.py` produced the campaign headline. It printed `DISTRUSTED = {DEGENERATE, SCHEMA_VIOLATION}` and `TRUSTED = everything else`. But `weekly_rescore.cohort_trust()` emits a THIRD verdict, **INSUFFICIENT** — the gate had too few rows to judge the cohort at all — and "everything else" swallowed it.  Measured on the live moat 2026-07-29: 887 INSUFFICIENT cohorts (2026-06-24 .…

### unknown_is_not_zero.md
*"R6 in one node — a value the instrument could not determine must never be rendered as 0, empty, absent, or fine; the fleet cited this 6x with no node behind it."*
**Rule R6 of the harness doctrine: unknown != zero.** When a measurement does not complete, the honest output is *"could not determine"* — never `0`, never `[]`, never `null`, never a green.  This node existed only as a citation for weeks. `kl_link_audit.py` measured it 2026-08-10: `6x unknown_is_not_zero <- no such node in ANY store`. The most-cited principle in the ledger had nothing behind the …

### vanity_domains_and_pivot_mechanics.md
*"The 11 owned vanity domains (Fly certs on app `mcplookup`), their verify status, and exactly what promoting one to primary requires (middleware flip is trivial; Clerk/DNS/SEO/branding are the real work)"*
2026-07-01: authoritative list from `flyctl certs list -a mcplookup` (Fly app `mcplookup`, db `mcplookup-db`; flyctl authed on tower at C:\Users\robin\.fly\bin\flyctl.exe).  **Current primary:** mcplookup.app (apex+www Issued). **11 vanity domains (apex / www status):** - mcprisky.io — Issued / Issued  ← FULLY VERIFIED, ready now - mcprisky.app — Not verified / Issued - mcpcheck.app — Not verified…

### vanity_domains_fly_redirect.md
*2026-06-28 11 vanity domains (+www) 301-redirect to mcplookup.app via Fly-owned redirect. Certs added; redirect middleware live; DNS at Porkbun pending.*
2026-06-28: Chairman bought 11 defensive/marketing domains, all to redirect to https://mcplookup.app. Chose Fly-owned redirect (not registrar URL-forwarding), apex + www.  Domains: mcprisky.io, mcprisky.app, mcpcheck.app, mcpcheck.cloud, mcpcheck.space, mcpcheck.one, mcpcheck.bot, mcpcheck.wiki, mcpchecker.app, mcpchecker.cloud, mcpchecker.wiki.  DONE: - 22 Fly certs added on app `mcplookup` (11 a…

### vast_budget_25_monthly.md
*Chairman set Vast GPU budget to $25/month (7/17/2026) — must last ≥1 month; per-run caps stay at $4*
7/17/2026: Robin upped the Vast budget to **$25 total** with the directive that it must last **at least one month** (i.e. through ~8/17/2026).  **Why:** Cost discipline while the 200K sprint drives large scoring volume. A full ScoreWave (~171K preds) costs ~$2.72-3 on a $0.33/hr 4090, so the budget is ample *unless* wasted by refires (7/17 push-fail burned $2.72) or wedged/idle instances left runn…

### vast_ledger_split_brain.md
*"vast_jobs ledger exists in TWO places — container state dir is EMPTY, tower repo holds the real 7/3 rescore launch row (no destroy row ever appended)"*
Found 2026-07-07 during vast-jobs-daily-audit: the managed vast-jobs ledger is split-brained.  - **Container** (`/home/workspace/zo_sentinel_state/vast_jobs/`): ledger has **0 rows**; dir contains only `last_audit.json`. Daily audit reads THIS ledger, so `open_runs` is structurally 0 regardless of reality. - **Tower** (`D:\zo\zo-sentinel\zo-sentinel`, state dir `\home\workspace\zo_sentinel_state\v…

### vuln_intel_spine_and_scan_shipped.md
*2026-07-02 PR*
2026-07-02 (late night): **Chairman urgency directive** ("compress timelines, add killer features to get users") → FATHER re-ruled (effort-gated work ships now, clocks start early, gates unchanged; earliest first-paid-key pulls to ~2026-08-01) → **PR #1189 shipped BOTH the horizon #1 (vuln-intel) AND the day-3 acquisition demo tonight.** Deployed Fly v44.  **Vuln-intel spine (in CONTRA's armor):**…

### vuln_surfaces_armed.md
*"2026-07-04 vuln.enabled + vuln.otx_enabled ARMED in prod after modeling; trust-cap regression fixed (123 capped); 3-layer \"switch had no lever\" chain found+fixed"*
2026-07-04 (chairman-directed): modeled both vuln surfaces dark, fixed contraindicators, ARMED both kill-switches in prod.  **Pre-flip modeling found 2 blockers:** 1. **Trust-cap regression**: Phase-1 tier backfill stamped raw student tiers → 8 official big-tech servers (azure-mcp, cloudflare/mcp, googleapis/gcloud-mcp...) published CRITICAL, violating the standing "verified publisher never above …

### watchdog_committed_copy_was_a_regression_of_the_running_one.md
*"ops/zo_mesh/watchdog.sh in git was v3.8, the running /home/workspace/zo_mesh/watchdog.sh is v3.9 — the repo copy was missing a fix for a tick-freeze outage, and its mtime was NEWER while its content was OLDER (PR #2404)"*
The dominant ledger class is *the artifact you inspected is not the artifact that runs*. This is the **inverted, more dangerous direction**: the artifact in git was older-BEHAVED than the artifact in production.  - repo `ops/zo_mesh/watchdog.sh` = **v3.8**, mtime 2026-07-02 - running `/home/workspace/zo_mesh/watchdog.sh` = **v3.9**, mtime 2026-06-14  **mtime lies about the direction.** The repo fi…

### watcher_became_the_outage_via_a_clock.md
*The FU-134 preflight aborted the nightly backup in ZERO seconds without calling flyctl once; the token worked the whole time. FU-137.*
**The organ written to EXPLAIN a failure became its only cause.** FU-134 established that flyctl's 720h client-side re-login timer had aged out and that the token was fine. A preflight was added to `db_backups/backup_zo_sentinel.py` to surface that. It reads `last_login` from `~/.fly/config.yml` and does `return 2` on EXPIRED **before making a single flyctl call**. The 2026-07-28 nightly started a…

### week_2_retry_charter_product_first_issues_on_github.md
*"2026-08-22..08-29 retry of the self-run week — product-first selection, chairman issues arrive as GitHub issues on rob531/zo-sentinel, hard meta cap, two lanes prompt-paused."*
**WINDOW OVER (08-29) — final scorecard in `chairman_briefing_2026-08-31.md`: the retry week was half-lost to a second tower-scheduler outage (08-24..08-30, FU-350 repeat); builder throughput continued but scored-MCP count stayed flat at 283,420. The issue-channel habit (poll `gh issue list` first) proved out and should OUTLIVE the window. The two prompt-paused lanes (autopoiesis-bar-tracker, goos…

### when_blocked_ask_what_you_already_hold.md
*"2026-08-06 — _tools/unblock.py maps a blocker to a resource you already have (4 live API keys, vast $14.77, 15 pre-built dark tools); built because three cycles ran and spent $0 of a funded account without ever knowing the account existed"*
Chairman, departing 2026-08-06: *"solve problems you MAY encounter in the many loops you run — remember all the resources you have, API KEYS (multiple), vastai etc. Think outside the box,"* and *"tools meant for a certain purpose can be used for different things."*  **The gap.** Three cycles ran that day and spent $0 of a funded account. Not a judgement failure — **no surface a lane reads had ever…

### which_copy_do_you_actually_run.md
*"the shared checkout's copy of deploy_prod.ps1 matched main by LUCK while 89 commits behind — a merge ended the luck"*
Before PR #2184 merged, `ops/host/deploy_prod.ps1` on disk at `D:\zo\zo-sentinel\zo-sentinel` was blob `ac127ebc` — **byte-identical to `origin/main`'s** — despite that checkout sitting 89 commits behind on branch `fix/fu137-triage-self-condemn`. The staleness was real the whole time and simply invisible, because that one file happened not to have changed.  The instant #2184 merged: on-disk `ac127…

### which_hosts_filesystem_enforces_your_invariant_for_free.md
*"The FU-209 class survived because on Windows the publisher already died at write_text — but the publisher runs on Linux, where the same write succeeds and commits the bad path."*
FU-209: a literal `<service_name>` committed as a **directory name** made `main` un-checkout-able on every Windows lane. The 2026-07-31 19:21Z slot removed the one artifact and honestly recorded that the emitter was unchanged. The 21:24Z slot closed the class in PR #2521 (`66c8c96a`): de-angle-bracketed `goose_recipes/service_dir_from_exemplar.yaml`, and added `_portable_path_violation()` at `zo_s…

### zero_rows_is_not_an_error.md
*"db_backups/_psql raises when `rc != 0 and not lines`, so a filtering SELECT makes the NO answer indistinguishable from a broken connection — the success case raises"*
`db_backups/backup_zo_sentinel.py::_psql` raises when `rc != 0 and not lines`. On the tower, flyctl exits **rc=1** carrying the harmless Windows trailer `Error: The handle is invalid.` (which `_clean()` already strips).  Therefore any existence check written as      SELECT 1 FROM pg_database WHERE datname='x'  returns **zero lines precisely when the answer is NO** — and the NO case is indistinguis…

### zo_mcp_connection_method.md
*Canonical way to connect Claude Desktop to Zo/ZoComputer — official gateway api.zo.computer/mcp via mcp-remote + zo_sk key as a local stdio server; supersedes the account-level connector*
2026-07-01: Switched the zo MCP connection OFF the flaky account-level connector (`newzocompconnect` / `newzocomputerJuly`, which connect-looped after the private→public flip — see [[zo_mcp_connector_oauth_loop]]) and ONTO Zo's official documented path (docs.zocomputer.com/mcp-server → Claude Desktop):  Local stdio server in `C:\Users\robin\AppData\Roaming\Claude\claude_desktop_config.json`: - ser…

### zo_mcp_connector_oauth_loop.md
*"zo MCP \"connect loops / never validates\" — server is healthy & no-auth; the loop is a stale account-connector registration, fix = remove+re-add (not reconnect), not an env var/redeploy"*
2026-07-01: Diagnosed the `newzocompconnect` (ZoComputer) desktop connector "connect loops — browser says connected, desktop never sees it valid" after the chairman toggled the ZoComputer app private→public.  **Connector endpoint = `https://zo-mcp-server-robinc.zocomputer.io/mcp`** (ZoComputer-platform public URL fronting the Modal container via Cloudflare; documented in zo-sentinel CLAUDE.md/READ…

### zocomputer_apt_tmpfile_broken.md
*"On ZoComputer (Modal container), apt-get update fails with \"Couldn't create temporary file /tmp/apt.conf.XXXXXX for passing config to apt-key\" across ALL repos. Not a per-repo issue — /tmp itself is unwritable for apt-key's invocation context. Workaround: install via static binary or direct download, skip apt."*
# ZoComputer apt-key /tmp failure  When running `apt-get update` (or any installer script that calls it, e.g. `curl ... | sh` installers) on ZoComputer, every configured repository fails with the same error:  ``` Couldn't create temporary file /tmp/apt.conf.XXXXXX for passing config to apt-key ```  Affects all repos uniformly: debian.org main + updates + security, grafana, github, nodesource, tail…

### zocomputer_vs_tower_topology.md
*CRITICAL topology — ZoComputer (Linux Modal container) vs the tower (Windows host) are DIFFERENT machines; don't conflate them; + go.sh/nohup gotchas*
Two distinct machines — I conflated them on 2026-06-29 and shipped a broken (mislabeled) deploy artifact, so pin this:  - **ZoComputer** = a **Linux** environment, an inner layer inside a **Modal container**. This is where `/home/workspace/...`, all the daemons (goose_runner, sentinel_directive_generator_goose, ladder_shim, etc.), `write_service` on 127.0.0.1:**8772**, mesh_memory, and `zm go` /…
