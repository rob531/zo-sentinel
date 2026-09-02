# RUN 1 — DISPOSITIONS (instructions for the follow-up-triage lane)

Machine-readable. One disposition per actionable entry. This file is handed to
`follow-up-triage` — the only writer of `status:` lines — to act on, not to read
as prose.

**Legend.** `KEEP` still true + worth doing (next action given). `MERGE` symptom
of a named parent. `SUPERSEDED` later work resolved it (cite evidence).
`WONTFIX` real but not worth it. `UNDETERMINED` cannot tell from the pack (say
what measurement settles it).

**Governing rule on SUPERSEDED (from the run charter).** SUPERSEDED is a claim
about the world and needs evidence, not plausibility. Rows below marked
`SUPERSEDED?` are **candidates**: triage must confirm the cited PR/commit/observation
is real and landed **before** writing a closed status. Do not bulk-close on this
file's say-so. Counting the no-status set all-closed loses real P1 defects;
counting them all-open inflates the ledger — both are wrong until measured.

**Coverage.** 43 no-status (§1) + 54 open P0/P1 (§2) + 114 open P2/P3/Punspecified
(§3) = 211 actionable entries.

---

## §1 — THE 43 NO-STATUS ENTRIES (the headline; measurement failure first)

These carry no `status:` field, so triage's sweep never saw them. **The first
action for every one is the same: assign it a status** so it becomes visible —
then apply the disposition. Read in full for this section (highest confidence).

| fu | pri | disp | parent/evidence | next action / measurement |
|---|---|---|---|---|
| FU-262 | P? | KEEP | — | Run next red floor probe; make it grade the artifact that RUNS, not a hand-written fixture |
| FU-263 | P? | KEEP | — | Wire the 20KB tool to a real caller; question it answers is stranded in a prod-path docstring |
| FU-264 | P? | SUPERSEDED? | recurrence keyed on family not free-text | Verify recurrence counter now keys on family id; the x3 read is the bug |
| FU-265 | P1 | UNDETERMINED | — | Measure whether any code path reads `PEER_CLEARABLE`; the value was unparseable, ruling non-executable |
| FU-267 | P1 | SUPERSEDED? | PR #2926 `eb910b33`, `_cid()` normaliser both ends, +29 tests | Confirm merge; negative control seen RED pre-fix / GREEN post |
| FU-268 | P1 | KEEP | — | `record_credit(state=...)` w/o `path=` must REFUSE, not default to destructive write; file clobbered twice |
| FU-269 | P1 | KEEP | — | Dashboard "474,689 scored" counts 191,273 unassessed; expose assessed pop. separately (Roadmap R1/R4) |
| FU-277 | P2 | SUPERSEDED? | `_fu108/scan_stranded.py` durable, 5 fixtures | Confirm the stranded-wave scan is now CODE, not re-implemented per run (ties to shepherd merge) |
| FU-289 | P? | SUPERSEDED? | six tools now `is_absolute() or exists()` | Confirm mount-root cwd no longer masks missing store |
| FU-290 | P? | SUPERSEDED? | probe now subprocesses real subject; control deletes file, requires rc=1 | Confirm falsification probe no longer inlines its subject |
| FU-298 | P? | KEEP | — | Architect salvage path logs fixed "proposed +0"; make salvage count visible instead of "reached nothing" |
| FU-304 | P? | SUPERSEDED? | PR #3106 wired chain into capmap-check, 14→12 dark | Confirm the seven-tool dead chain now has a live consumer |
| FU-305 | P? | SUPERSEDED? | PR #3124 `fec100e9`, 26 alarm tests now collected | Confirm the halt's tests are collected by a required check |
| FU-306 | P? | SUPERSEDED? | `friction.py --pysrc` landed; falsification 2026-08-17 | Confirm the cure is reachable from the PowerShell surface it was bitten at |
| FU-309 | P? | KEEP | — | Gate: assert no PR changes `services/active/<n>/` without a `service.toml`; 16 PRs born unmergeable, ~3,500 unique lines |
| FU-310 | P? | KEEP | — | ledger_lint entry-span is drawn wrong; write a no-key fixture and require E3/E5/E6 to flag it |
| FU-311 | P? | SUPERSEDED? | PR #3172 `70008eaa` | Confirm capmap-check runs graph_gap_directives explicitly; default input was stale |
| FU-312 | P? | SUPERSEDED? | `_verified_count()` requires int, negative-control seam | Confirm rollback selector no longer consumes an error string as a row count |
| FU-313 | P? | KEEP | — | Verify must discriminate on the artifact EXISTING, not on safe state; ACTED row swept GREEN with no artifact |
| FU-314 | P? | KEEP | — | v4 selector must target by NAME, not iteration order; population grew in front of positional selection |
| FU-315 | P? | KEEP | — | Revert probe must use cached/`--depth` sandbox; full-clone cost grew from feasible to unreachable |
| FU-318 | P? | SUPERSEDED? | all 7 SKILL.md exemplars rewritten; `--pysrc` entry point | Confirm no prompt still hands the unsafe copy-and-run form |
| FU-331 | P2 | SUPERSEDED? | stranded_scan keys on (root, run_id); FAILED_NO_DATA bucket | Confirm scan discovers sibling roots; the 0-with-passing-control was the bug |
| FU-334 | P? | SUPERSEDED? | selector windowed 7d on `friction.row_key()` | Confirm headline counts the same population its predicate grades (51% class) |
| FU-335 | P? | SUPERSEDED? | MAX_CLI_WAIT_S 45→25 across 17 sites | Confirm the emitted wait is no longer the clamp's own bound |
| FU-338 | P? | KEEP | — | Revert apparatus must not demote COMPLETE undetected; partial revert priced as total, rc=0 re-armed destructive revert |
| FU-341 | P? | SUPERSEDED? | `friction.tower_invisible()` classifies both spellings | Confirm scratchpad guard is not blind to its own subject / disarmed by a stray token |
| FU-342 | P? | KEEP | — | Money guard tighter than the wedge guard behind it; FU-104 startup allowance is unfunded — widen the band |
| FU-343 | P? | SUPERSEDED? | classifier wired into all 9 tower-side doors | Confirm census shows the cure at every call site of that shape, not one of eight |
| FU-344 | P? | KEEP | — | `REVERT_FAILED` reads 0 because it is transient/swept; write a durable marker so the sweep can see it |
| FU-345 | P? | SUPERSEDED? | `friction.unbuffered_argv()`+`progress_basis()` | Confirm a RUNNING verdict differs at second 2 vs minute 16 |
| FU-351 | P? | SUPERSEDED? | `_write_receipt()` reloads at write time; lost-write control in FLOOR | Confirm the lane_receipts lost-update is closed |
| FU-352 | P? | SUPERSEDED? | lane_check_in passes `--no-detach`; verify rc=0 | Confirm the mandated check-in no longer scores UNKNOWN each cycle |
| FU-353 | P? | SUPERSEDED? | PR #4327 `60d85810`; daemon restart tested 08-31 | Confirm the bar CSV owner/basis is stable and the detached child is reboot-proof |
| FU-361 | P1 | KEEP | — | **Corpus floor frozen 70d** (Roadmap R2). Run oldest_scored_at movement probe; fix cohort SELECTION, not `--refresh-cap` |
| FU-370 | P? | SUPERSEDED? | baseline note forbade the 277→335 move by name | Confirm; **do not re-propose this move — it is a dead cure** |
| FU-371 | P? | SUPERSEDED? | PR #4375; component in all 3 doors, census 2-of-35 | Confirm USED/UNUSED census can now tell landed from unused (fix comms, not a gate) |
| FU-376 | P? | SUPERSEDED? | `_tools/roster_refresh.py` derives roster from live mirror | Confirm the 10-day-TTL roster with no writer is fixed; it collapsed every lane's cadence window |
| FU-377 | P? | KEEP | — | Wire `friction.self_detach()` into `dark_tools.py` main(); a 282s bare tool can't finish inside the MCP transport cut |
| FU-378 | P? | SUPERSEDED? | both missed slots inside acknowledged dormancies | Confirm the weekly counts only unacknowledged misses; it cannot observe its own dormancy via its scheduler stamp |
| FU-379 | P? | KEEP | — | Run the paired control the metric's own comment prescribes (writer ALIVE); it was written by a different module |
| FU-380 | P? | SUPERSEDED? | truncation is upstream in the SQLAlchemy writer (see FU-379) | Downstream fix impossible; the leaderboard ranks message LENGTH — reframe onto FU-379's writer fix |
| FU-382 | P? | KEEP | — | Floor probe diffed a SHARED dir with no ownership key; carry an identity key and return rc=2 on launch failure |

**Net of §1:** ~18 KEEP (live defects, several P1), ~24 SUPERSEDED-candidates
(triage must verify the cited PR before closing), 1 UNDETERMINED (FU-265). The
P1 close-calls to get right: **FU-269 and FU-361 are live and unfixed** (both
feed the roadmap); **FU-267 is a real merged fix** (verify #2926); **FU-268 is a
live destructive-default hazard** (file clobbered twice); **FU-265 needs one
measurement** (does any path read `PEER_CLEARABLE`).

---

## §2 — OPEN P0/P1 (54 entries; the 5 no-status P1s are in §1)

Corrections applied to the raw digest where SUPERSEDED lacked in-text evidence
(FU-054/207/226/251/254/255 downgraded to KEEP; FU-119 to UNDETERMINED).

| fu | pri | disp | parent/evidence | next action / measurement |
|---|---|---|---|---|
| FU-093 | P0 | KEEP | fix shipped PR #1804, canary verified | Re-verify tail predates fix (Roadmap R3); chair decision on 3-batch rollback of pre-fix garbage |
| FU-235 | P0 | KEEP | header open; content notes migration privilege wall | Chair decision: pick migration-owner option A/B/D, not the unsafe C that flew |
| FU-001 | P1 | KEEP | — | Make wave/rescore harnesses write the run ledger; merge PR #1639 + wire POST hook |
| FU-024 | P1 | KEEP | — | Nightly backup: add retry window + staleness alarm for Fly control-plane outage |
| FU-027 | P1 | KEEP | warm 48s→4s done | Fix /freshness cold path (~55s); warm-path fix only was partial |
| FU-028 | P1 | KEEP | — | Container runtime drift: add scheduled refresh + alarm |
| FU-036 | P1 | MERGE | FU-021 | Shared watchdog roster fix in go.sh; both are daemon-liveness defects |
| FU-054 | P1 | KEEP | row goal met — reframe | Intake maintenance, NOT the 200K gate (registry at 250%); keep discovery alive so it never re-collapses |
| FU-058 | P1 | KEEP | — | 99.5% HIGH/CRITICAL — run risk_tier_threshold_calibration_probe (Roadmap R4) |
| FU-065 | P1 | KEEP | — | Runtime chronically behind main; resolve deploy cron/description mismatch |
| FU-072 | P1 | KEEP | parent of mount/KL sub-tasks | Steer files→services (SOA) as one program, not independent edits |
| FU-075 | P1 | KEEP | — | conduid competitive response — execute FU-076/124/077 (Roadmap R5) |
| FU-076 | P1 | KEEP | 4/7 legible axes live | Complete FU-080 signal ingestion; ship the scorecard (Roadmap R5) |
| FU-090 | P1 | KEEP | — | 230K unscored after discovery doubled the moat — folds into Roadmap R2 rescore |
| FU-099 | P1 | KEEP | learning | Standing lesson: drive the spine-unblocking PR; never rationalize a red check as cosmetic |
| FU-101 | P1 | KEEP | — | Shepherd role: cross-pollinate scheduled tasks; break internal walls |
| FU-104 | P1 | KEEP | — | Score run 45843424 exited SILENT — investigate the silent-exit path |
| FU-107 | P1 | KEEP | — | Backup INFEASIBLE at size (~68 rows/s ⇒ ~8h); optimize COPY or split (ties FU-081) |
| FU-108 | P1 | UNDETERMINED | — | Measure whether weekly_rescore now has an import phase; the 07-26 score may already be landed |
| FU-109 | P1 | KEEP | — | 21 staged services HOLD on liveness gate; merge PR #2060 + verify the promoter is a real check |
| FU-115 | P1 | KEEP | — | Read daemon health from process, not log files (healthy daemons read dead for hours) |
| FU-116 | P1 | KEEP | PR #2058 | Wire the goose-canary's two guards as GATES, not committed files |
| FU-117 | P1 | KEEP | — | goose doesn't fail on stdio-bridge startup failure; add FATAL on attach-failure |
| FU-119 | P1 | UNDETERMINED | — | Measure whether the goose-canary now drives the ladder shim, not a direct provider transport |
| FU-124 | P1 | KEEP | — | Crawler indexability (Roadmap R5) — the one axis conduid is unambiguously ahead |
| FU-134 | P1 | KEEP | — | flyctl 720h re-login timer aged out; re-authenticate + add expiry alarm |
| FU-149 | P1 | KEEP | — | flyctl token lost on tower; obtain new token (blocks every prod-PG tunnel) |
| FU-159 | P1 | KEEP | controlled 2-directive test | Directive quality does NOT bypass Tier-0 merge path — enforce via gates |
| FU-167 | P1 | KEEP | suppressed 17× | Gate passes rc=0 while printing its own escalation trigger — wire the suppression correctly |
| FU-190 | P1 | KEEP | — | 18,560 "Recorded OSV vuln" lines, table holds ZERO; fix ws_write returning None |
| FU-207 | P1 | KEEP | cited 13×, still open | Scheduled task lastRunAt advanced 5 slots doing no work; cadence cut treated the symptom only |
| FU-210 | P1 | SUPERSEDED? | PRs #2524/#2521/#2522 | Confirm the slot grid was rewritten and no longer pages on deleted slots |
| FU-226 | P1 | KEEP | stale Phase-1 labels still in prompt | Retire prod-drift's residual STAGE-never-FIRE labels (see Lane Verdict B) |
| FU-228 | P1 | KEEP | — | write_service silently caps every unbounded SELECT at 200 rows; add a truncation flag |
| FU-232 | P1 | KEEP | — | The 4 tools this lane RUNS diverged from the 4 the repo TESTS; unify (untracked half is bigger) |
| FU-236 | P1 | KEEP | — | Liveness contract passes 75-byte comment, no-hollow passes 32-byte stub; make the gate prove function |
| FU-238 | P1 | KEEP | — | CLERK_WEBHOOK_SECRET staged, never deployed; deploy alongside FU-235 or webhook 503s |
| FU-239 | P1 | KEEP | — | Clerk reconcile has no host; COPY tools/ into image + put key on tower |
| FU-242 | P1 | KEEP | — | 9 FU predicates query tables absent at :8772 (score_runs/servers/server_scores); fix or re-point |
| FU-245 | P1 | KEEP | — | Clerk webhook delivered 0 of 3 since 06-27; debug delivery (backfill masks it green) |
| FU-248 | P1 | KEEP | — | The loop measured its product, never itself; measure the loop's own stall sources |
| FU-251 | P1 | KEEP | — | 23 of 34 lanes drive desktop via Windows-MCP (records silent failures); name Desktop Commander |
| FU-253 | P1 | KEEP | — | Lane halt ARMED + UNCONSULTED 5 days; wire a lane to READ the sentinel |
| FU-254 | P1 | KEEP | — | 15 of 88 tools consulted by nothing; run USED/UNUSED census (existence ≠ adoption) |
| FU-255 | P1 | KEEP | doctrine | Standing principle: CODE steers, PROSE does not — keep as a rule, not a task |
| FU-272 | P1 | KEEP | PR #2920 | Builder emits one service as two PRs into two incompatible lifecycle dirs; unify |
| FU-302 | P1 | SUPERSEDED? | verb-to-end-the-alarm added | Confirm the unrevertable peer decision now has a closing verb (16-lane 48h alarm) |
| FU-303 | P1 | KEEP | — | fu_verify hangs on Windows (reason peer_review already fixed); Phase 2a produced no data |
| FU-321 | P1 | KEEP | PR #4302 (pr-open) | A failed wave became "newest unfinished run" forever, damming every rescore — merge |
| FU-326 | P1 | KEEP | PR #3209 (pr-open) | Dam fix enumerated failure NAMES; next wave returned an unlisted name — enumerate classes |
| FU-346 | P1 | KEEP | — | Heading FORM (not level) makes an FU invisible to fu_verify; fix form detection |
| FU-350 | P1 | KEEP | — | Tower scheduler dead 7 days; every instrument that would say so was itself a scheduled task — external watch |
| FU-354 | P1 | KEEP | in-progress | e2e app-e2e-parity gate RED 4 nights; debug duckdb-vs-postgres cutover parity |
| FU-364 | P1 | KEEP | — | Lane's whole remit (`--may registry_insert`) has returned UNKNOWN ACTION 8+ days; name the action in authority.json |

---

## §3 — OPEN P2 / P3 / Punspecified (114 entries)

Lower stakes. Full per-entry candidate dispositions below. **Every `SUPERSEDED?`
in this section is a candidate** — the P2/P3 pass had weaker evidence than §1/§2,
so triage must confirm the cited parent/PR before closing. Known corrections
from the theme data are applied: FU-055 → KEEP (roadmap-relevant, not superseded
by FU-030); entries that are actually P0/P1 or already-closed were removed to §2
or dropped.

**Roadmap-relevant P2s (do not let these get lost in the tail):**

| fu | disp | next action |
|---|---|---|
| FU-055 | KEEP | "Scoring is SATURATED; the 200K metric measures the wrong thing" — the thesis behind Roadmap R1 |
| FU-077 | KEEP | Rebuild the mcprisky.io landing for conversion (Roadmap R5) |
| FU-078 | KEEP | Evaluate a builder-side flywheel (claim/verify/analytics/monetize) — conduid has one (R5) |
| FU-080 | KEEP | Ingest legible signals (maintenance-age, scoped-perms, tests, pinned-deps) into app tier (R5 blocker) |
| FU-092 | KEEP | CVE-axis strengthening (family propagation + linker v2) — the sparsest, most differentiating signal |
| FU-237 | KEEP | Freshness SLA and scoring cadence both 7d; decouple so a wave landing doesn't self-breach |
| FU-058→R4 | KEEP | (P1, in §2) risk-tier degeneracy — calibrate |

**Standing-defect P2/P3 tail (KEEP unless noted).** The following are real,
lower-stakes defects to keep on the backlog with the named next action; none is
worth a WONTFIX given cost, and none has evidence of resolution:

FU-022, FU-023, FU-035, FU-040, FU-042, FU-043, FU-044, FU-045, FU-046, FU-049,
FU-050, FU-051, FU-057, FU-059, FU-060, FU-062, FU-066, FU-069, FU-070, FU-071,
FU-073, FU-074, FU-083, FU-085, FU-086, FU-089, FU-091, FU-096, FU-098, FU-100,
FU-102, FU-114, FU-118, FU-125, FU-128, FU-135, FU-139, FU-142, FU-150, FU-152,
FU-154, FU-160, FU-161, FU-162, FU-164, FU-168, FU-169, FU-172, FU-177, FU-188,
FU-193, FU-194, FU-195, FU-200, FU-218, FU-244, FU-252, FU-261, FU-274, FU-282,
FU-284, FU-287, FU-295, FU-300, FU-301, FU-308, FU-316, FU-320, FU-322, FU-325,
FU-327, FU-330, FU-340, FU-347, FU-348, FU-355, FU-356, FU-363, FU-365, FU-367,
FU-372, FU-374 — **all KEEP** (each carries a named next action in the P2/P3
digest; see `14_open_p2p3_digest.md` for per-entry detail).

**SUPERSEDED? candidates (verify the cited parent, then close):**
FU-018→FU-005 class, FU-038→FU-367 baseline, FU-041→FU-077, FU-084→stale-branch
sweep, FU-153→FU-138, FU-178→FU-082, FU-197→FU-193, FU-283→FU-287, FU-349→FU-355,
FU-353→FU-355. Triage: confirm each parent actually resolves the child before
writing `resolved`.

**WONTFIX (real but not worth the cost):**
- FU-047 — dead directive schema fields (reads[]/complexity/exemplar); remove only if touched.
- FU-067 — local clone 157-entries dirty; resync from main before next commit, don't chase.
- FU-101(gov)/FU-128 — branch-local workarounds; documented, low value to harden.

**UNDETERMINED (name the measurement):**
- FU-019 — is `directives/` a live queue or litter? Measure before deciding clean-on-deploy.
- FU-279 — FAMILY A 0→6 sites: real regression or classifier scope shift? Re-run explicit lookup.
- FU-280 — is the `--enforce` hold still live? Audit the falsification trail.
- FU-299 — was the 21-site fall real or a classifier-scope artifact? Re-measure app.models vs app.models+app.db.
- FU-363 — is the error-message-invited repair itself the regression? (Do not accept an error's suggested fix blindly — dead cure.)
- FU-365 — is a "LEFT ALIVE" instance billing forever? Read the launcher's own fire.err state.

---

## Disposition tally (for the triage lane)

| | KEEP | SUPERSEDED? | MERGE | WONTFIX | UNDETERMINED |
|---|---|---|---|---|---|
| §1 no-status (43) | ~18 | ~24 | 0 | 0 | 1 |
| §2 open P0/P1 (54) | ~50 | 2 | 1 | 0 | 2 (FU-108, FU-119) |
| §3 open P2/P3 (114) | ~86 | ~10 | 0 | ~4 | ~6 |

The single most important instruction: **before closing any SUPERSEDED?, run the
cited evidence.** The second: **assign a status to all 43 §1 entries so the
sweep can see them, then fix the sweep to catch no-status entries in future**
(Roadmap R6) — otherwise this exact blind spot regenerates.
