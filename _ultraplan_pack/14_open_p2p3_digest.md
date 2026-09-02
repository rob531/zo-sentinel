# TIER 2 -- OPEN, BELOW P1, ONE PARAGRAPH EACH

Built 2026-09-02T21:50:58+00:00. 114 entries: 82 P2, 21 P3, 11 with NO priority field.

The last group is not P3. An entry with no priority was never triaged, and calling this file 'P2/P3' would have hidden them inside a rank nobody assigned.

Full text lives in FOLLOWUPS.md; read it there if one of these becomes load-bearing.

### FU-018 [P3/open] Two sibling gaps found while wiring has_known_cve
filed 2026-07-19 (45d) · last touch 2026-07-19 (45d) · source FU-005 implementation (PR #1642 body) · logs 0 · verify yes
Surfaced by the FU-005 fix, deliberately not silently changed. (a) `referenced_in_threat_intel`, the sibling boolean facet, still validates-but-drops in compile_filters — the identical bug class FU-005 just fixed, same-shape fix available. (b) threat_intel_summary_api (newly mounted by #1642) reads threat tables WITHOUT the kill-switch honest-degrade that server_threat_refs has, so with vuln disab

### FU-019 [P3/open] Decide whether the runtime clone should ever be fully clean
filed 2026-07-19 (45d) · last touch 2026-07-19 (45d) · source FU-007 implementation · logs 0 · verify NO
FU-007 fixed the ff-blocking case but deliberately left the bigger question to a chairman: the runtime clone holds 16,563 untracked files, and they are NOT all litter — directives/ (3,395) is the live builder queue, and .ingestor_enabled / .pr_publisher_enabled / .build_registry.json / api_keys.txt / zo_sentinel_app.db are referenced by tracked code, with 28 daemons running against that cwd. A tru

### FU-022 [P2/open] rug_pull_monitor snapshots silently failing (write_service 500s)
filed 2026-07-19 (45d) · last touch 2026-07-19 (45d) · source FU-016 verification · logs 0 · verify yes
During the verified cycle 1, EVERY store_snapshot write to mcp_definition_history returned 500 from http://127.0.0.1:8772/write, while heartbeat writes to service_health on the same endpoint succeeded — so the monitor runs and reports healthy while persisting nothing. Its core detection memory (definition history = the rug-pull baseline) is impaired, and the health signal does not reflect it. writ

### FU-023 [P2/open] Shared backup dir is a clobber hazard between agents
filed 2026-07-19 (45d) · last touch 2026-07-19 (45d) · source FU-017 incident · logs 0 · verify yes
_followup_backups/<date>/ is shared by every follow-up worker, is untracked, and holds loose pre-change copies whose names do not say WHEN or WHY they were taken. On 7/19 this cost real work twice: one agent's backups were deleted mid-run by another, and at 19:28 four live harness files were overwritten with stale pre-#1643/#1645 copies restored from it (healed from origin/main). Interim mitigatio

### FU-040 [P2/in-progress] Gaps map harvests EXEMPLAR filenames as build targets, and 3 of the 6 PHASE 9 lanes name dangling/quarantined exemplars
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source daily-chairman-review (adversarial verification pass) · logs 5 · verify yes
Two defects, both surfaced by fact-checking my own PHASE 9 refill (#1670) rather than by any gate. **(1) EXTRACTOR BUG — the gaps map invents targets.** After the refill the live gaps map returns **7** buildable targets, not the 6 specified: the extra is **`schema_prm_guard.py`**, which exists nowhere in the repo and appears in PRODUCT_SPEC.md *only as the Exemplar* for `edit_class_directive_valid

### FU-042 [P2/open] Hollow gate matches inbound-only, so 87.3% of orphans pass it
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (REACHABILITY_POSTMORTEM_2026-07-19) · logs 0 · verify yes
`zo_sentinel/gates/hollow.py` asks "is this module connected to the DB?" and never "is anything connected to this module?" Its `REAL` regex is `from app\.db|from app\.models|import app\.db|app\.models import|get_session|from app import|import verdict_breakdown_api`. Measured against the graveyard: of 371 orphans only **47 were flagged and 324 (87.3%) passed**; of the 175 exposing an `APIRouter`, *

### FU-043 [P2/open] ui-smoke's path filter skips 67% of builder PRs
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (REACHABILITY_POSTMORTEM_2026-07-19) · logs 0 · verify yes
`ui-smoke.yml` triggers on paths `**/*.html`, `app/**`, `perspective_*.py`, `ask_*.py`, `facet_enum_service.py`, `config_scan_api.py`. Builder modules land at repo ROOT with arbitrary names, so they match none of them. Sampled over the last 60 merged builder PRs: 20 would trigger ui-smoke, **40 (67%) were skipped by the path filter**. Worse, on the 20 that did run the treewalk visits the existing 

### FU-044 [P2/open] app/main.py's mount loop swallows every mount failure
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (REACHABILITY_POSTMORTEM_2026-07-19) · logs 0 · verify yes
`app/main.py` iterates `_OPTIONAL_ROUTERS` with `except Exception: pass  # loose/unbuilt feature module -- skip, never block boot`. A module that fails to IMPORT is therefore indistinguishable from one that was never wired, at the exact layer where the difference matters. Given FU-031's finding that a large share of merged modules raise `ImportError` on `app.models`, this `pass` is very likely hid

### FU-045 [P2/open] Retire merge/survival rate for load-bearing yield; treat a never-failing gate as an alarm
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (LADDER_ATTRIBUTION_AUDIT + REACHABILITY_POSTMORTEM 2026-07-19) · logs 0 · verify yes
Two measurement changes from the same evidence. **(a) The tracked metrics are vanity.** Week of Jun 15 read 163 opened / 143 merged (88%) / 113 alive (69%) -- the best week on every dashboard number -- while producing **0 load-bearing modules, 0% yield**: 156 modules merged clean, passed no-hollow, still exist, and nothing has ever called one. Replace with **load-bearing yield = live routes gained

### FU-046 [P2/open] The builder has never written a surviving test
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (LADDER_ATTRIBUTION_AUDIT_2026-07-19) · logs 0 · verify yes
`tests/` is 113 files / 18,368 lines at **0.0% builder** by git-blame line survival. The audit ties this straight to the adoption failure: "it could produce a module but not the evidence that the module worked -- which is precisely why 386 modules went un-adopted." Adjacent and equally stark: `zo_sentinel/` is 41 files / 9,536 lines at **0.0% builder**, and `tools/` + ops + CI is 116 files / 10,93

### FU-047 [P3/open] Directive schema fields reads[], complexity and exemplar are dead weight
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (LADDER_ATTRIBUTION_AUDIT_2026-07-19) · logs 0 · verify NO
Three fields measured across 5,924 directives. `reads[]` is present on 277 with **no measurable lift** on merge or survival -- the standing 'placebo' note is now confirmed rather than suspected. `complexity` is **uniformly null** on the joinable set, so it could not be correlated with outcome at all. `exemplar` appears on **0 directives** -- the one doctrine that demonstrably works (exemplar enfor

### FU-048 [P2/open] The 1.43 plan's pre-flip verifications were never run, and the flip has shipped
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (GOOSE_WATCH.md) · logs 0 · verify NO
Three GOOSE_WATCH rows carry verification instructions worded as preconditions for the 1.43 flip, and the flip executed 2026-07-19 (FU-017, #1652) without them. (a) "Re-test our SSE emitter against the 1.43 parser **before flip**" covering #10258 (empty finish_reason non-terminal) and #10023 (streamed tool calls without indexes) -- both touch the shim's SSE emitter directly. (b) The thinking-strea

### FU-050 [P3/open] The architect's log inode keeps being deleted mid-run
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (chairman_briefing_2026-07-20) · logs 0 · verify yes
The architect's log inode was deleted again today, leaving the live log readable only via `/proc/<pid>/fd/1`. It is filed as a recorded scar, but it keeps recurring, which makes it a defect rather than a scar: the single most important diagnostic surface for FU-006 (rung convergence) is unavailable by default and recoverable only by someone who already knows the trick. It compounds two other entri

### FU-051 [P3/open] Institutionalize the adversarial verification pass on the daily briefing
filed 2026-07-20 (44d) · last touch 2026-07-20 (44d) · source triage sweep (chairman_briefing_2026-07-20) · logs 0 · verify yes
A read-only subagent run explicitly to find errors in the 07-20 briefing draft found **five**, and **four of the five were errors in the author's favour** -- they made the day's work look tidier than it was. The five: the flat-scoring day count; "the gaps map resolves to exactly 6" (it resolved to 7, which became FU-040); the mcplookup.app 200 claim (now FU-049); "refill verified end-to-end"; and 

### FU-038 [P2/open] capmap-check has blocked two agent PRs for a day on pre-existing schema drift
filed 2026-07-20 (44d) · last touch 2026-07-21 (43d) · source daily-chairman-review · logs 1 · verify yes
PRs **#1641** (`axis_score_summary_router`, opened 07-19T18:50Z) and **#1628** (`build_import_row_delta_audit`, opened 07-19T12:56Z) are both BLOCKED with every check green except `capmap-check`, which fails as: `verdict: DRIFT (drift=6 orphaned_ui=4 gap_areas=3)` / `SCHEMA DRIFT (6) -- endpoint touches a table not in app.sql`, first named example `[Registry/Assessment] GET /servers/{server_id}/ov

### FU-059 [P2/open] A delta rescore changed exactly 32,545 servers on six of seven axes — identical counts, independent axes
filed 2026-07-21 (43d) · last touch 2026-07-21 (43d) · source zo-sentinel-pipeline-watch · logs 1 · verify yes
Run `20260719-003024`'s `delta_summary` over 65,045 rescored servers reports, per axis, `changed` / `unchanged`: overall_risk **32,545 / 32,500**; auth_strength **32,545 / 32,500**; data_sensitivity **32,545 / 32,500**; network_egress **32,545 / 32,500**; maintainer_trust **32,545 / 32,500**; exploit_surface **32,545 / 32,500**; capability_breadth 32,426 / 32,619. Six of seven axes moved on **byte

### FU-060 [P2/open] "did NOT reach propose_directive" is asserted, not measured — the bridge's return value is logged nowhere
filed 2026-07-21 (43d) · last touch 2026-07-21 (43d) · source deploy-runtime-from-main · logs 2 · verify yes
The generator classifies every `+0` cycle with a fixed string: `ARCHITECT NON-CONVERGENCE (zero_proposed): ... proposed +0 -- did NOT reach propose_directive (tool-call loop / over-exploration); rc=0`. That sentence names a **cause**, and on at least one cycle today the transcript contradicts it. At 09:12:53Z the captured goose stdout contains a rendered tool invocation — `▸ propose_directive zo_d
cites: FU-006, FU-037

### FU-066 [P2/open] The builder ladder's tier-1 rung produced hollow scaffolds on 2 of 2 attempts and nothing reports it
filed 2026-07-21 (43d) · last touch 2026-07-21 (43d) · source daily-chairman-review · logs 1 · verify yes
Both directives the factory attempted today followed an identical path: goose reported success on the tier-1 rung, the `no_hollow` gate BLOCKED the output, ghost-guard refused to complete, and the deterministic `zo-ladder-high` engine then wrote the file and the build proceeded. Verbatim for `verdict_distribution_summary_api` at 12:01:50Z — `Goose execution succeeded` → `[no-hollow] BLOCKED -- hol
cites: FU-006, FU-031

### FU-067 [P2/open] The local zo-sentinel clone at D:\zo\zo-sentinel\zo-sentinel is 157 entries dirty and unsafe to commit from
filed 2026-07-21 (43d) · last touch 2026-07-21 (43d) · source daily-chairman-review · logs 0 · verify yes
`git status --short` in `D:\zo\zo-sentinel\zo-sentinel` reports **157 entries**, the large majority staged as ADDED (`A tools/reachability_ratchet.py`, `A tests/test_reachability_ratchet.py`, ~130 root-level `*_api.py` / `*_router.py` modules, plus `M app/main.py`, `M PRODUCT_SPEC.md`, `M goose_recipes/*.yaml`, `M .github/workflows/pr-gates.yml`). Files that are demonstrably merged on main show as

### FU-070 [P3/open] The architect prompt contradicts itself on whether wiring is in scope
filed 2026-07-21 (43d) · last touch 2026-07-21 (43d) · source triage sweep (INTEGRATION_SURFACE_STRATEGY_2026-07-20 §4.2) · logs 0 · verify yes
`goose_recipes/directive_architect.yaml` states that the live build queue is the 3-tier app surface *"and the app-assembly wiring"*, then two paragraphs later states *"DO NOT PROPOSE ... the app spine / app/main.py wiring"*. Both sentences are in the prompt today. A weak model resolves that arbitrarily, which is a plausible contributing cause of a behaviour already well evidenced elsewhere: the ar

### FU-020 [P3/open] Residual harness cleanups from the FU-011/FU-015 fixes
filed 2026-07-19 (45d) · last touch 2026-07-22 (42d) · source FU-011 + FU-015 implementation · logs 1 · verify yes
Three known-and-scoped leftovers, none blocking. (a) 14 top-level directives/*.bak* sentinel-copies outside the queue dirs — same janitor treatment, not moved because they were outside FU-011's scope. (b) The .bak exclusion should probably extend to goose_runner.load_directives and queue_janitor._iter_directive_files (same *.json glob class, not named in the tasking). (c) FU-015's ghost-edit guard

### FU-071 [P3/open] The KL-artifact pattern was built once, for DB schema, and never for the other four surfaces
filed 2026-07-21 (43d) · last touch 2026-07-22 (42d) · source triage sweep (INTEGRATION_SURFACE_STRATEGY_2026-07-20 §0/§3) · logs 2 · verify yes
Builder-lane-shaped; recording as a spec/anchor target rather than hand-building. `schema_kl.py` is a working template that does three things — introspects live truth from `app.models`, persists a versioned artifact (`graphify-out/schema_kl.json`), and enforces it as a pure-AST linter that `goose_runner._schema_prm_gate` can run with no DB to bounce a hallucinated schema *before* the build is acce
cites: FU-039, FU-069

### FU-073 [P2/open] Runtime app/main.py keeps accruing uncommitted local wiring that safe_ff stashes and silently discards each deploy
filed 2026-07-22 (42d) · last touch 2026-07-22 (42d) · source deploy-runtime-from-main · logs 0 · verify yes
Second consecutive deploy where `app/main.py` on the ZoComputer runtime clone (`/home/workspace/zo_sentinel`) carried **tracked, uncommitted local wiring** that safe_ff auto-stashed and archived away — so the edits are non-destructively saved but no longer live and never made it into a PR. Today's stash (`zo_sentinel_state/stash_archive/20260722T091040Z.patch`, `stash@{0}`) held two edits to `_OPT
cites: FU-019, FU-039, FU-065, FU-067, FU-069, FU-072

### FU-077 [P2/open] Rebuild the mcprisky.io landing for conversion (live ticker, credit-score frame, scorecard hero)
filed 2026-07-22 (42d) · last touch 2026-07-22 (42d) · source chairman (attended) — conduid competitive assessment · logs 0 · verify yes
conduid's landing converts on sight: a live "46.7K servers scored" counter, credit-score framing ("One Number. Instant Clarity." with 80-100/50-79/0-49 bands), an animated per-server scorecard in the hero (auth/transport/maintenance visible), a scrolling top-rated list, and a tight problem→solution→CTA with SEARCH front-and-centre. Ours is a static 200K robot graphic whose campaign produced ~zero 
cites: FU-041, FU-054, FU-075, FU-076

### FU-078 [P2/open] Evaluate a builder-side flywheel (claim / verify ownership / analytics / monetize) — conduid has one, we have none
filed 2026-07-22 (42d) · last touch 2026-07-22 (42d) · source chairman (attended) — conduid competitive assessment · logs 0 · verify yes
conduid is two-sided: authors "claim" their server via GitHub OAuth (ownership auto-verified when GH username matches the repo owner, else a repo file or manual review), unlocking an analytics dashboard (installs/usage via an SDK tracking endpoint), an editable listing, a verified-ownership badge (+up to 15 score pts), and a monetization path ("enable paid subscriptions"). That is a growth + defen
cites: FU-054, FU-075

### FU-008 [P3/watch] PR dedup gate let a same-name pair through
filed 2026-07-18 (46d) · last touch 2026-07-23 (41d) · source chairman briefing · logs 6 · verify yes
#1566/#1569 (never_scored_burndown_api) both merged — dedup gate admitted a same-name pair. Instruction was watch, don't churn. Trigger condition: a second duplicate pair → tighten the dedup gate.

### FU-039 [P2/watch] CofC 2026-07-20: wire/mount lane DEFERRED to post-census — ruling recorded, triggers set
filed 2026-07-20 (44d) · last touch 2026-07-23 (41d) · source daily-chairman-review (Council of Claudes 3+FATHER) · logs 4 · verify NO
Council convened on: should we build a wiring/mount lane today, fix the architect's emission contract today, or fix observability only and decide the lane after the 07-21 census? Seats: (1) ARCHITECT'S ADVOCATE — `output_file: null` is the switch that disables the verification apparatus, fix at emission with `handler=edit_file` + required `target_file` + an AST post-condition, and key the validato

### FU-041 [P3/watch] 200K LinkedIn campaign is at ~zero engagement — do not renew the daily-repost pattern after the 07-22 window close
filed 2026-07-20 (44d) · last touch 2026-07-23 (41d) · source mcprisky-200k-daily-repost (scheduled) · logs 5 · verify yes
Four consecutive daily posts (07-17..07-20) to 1,491 followers have produced **zero comments, ever**, and 1-3 reactions each. Impressions by post: 79 (07-17), 150 (07-18, the only one with 3 reactions), 75 (07-19), i.e. no upward trend and the newest is the weakest. The 07-17 Discord cross-post in #ai-coding (Zo Computer Club) has **zero reactions and zero replies** three days on; the only later m

### FU-080 [P2/open] Ingest the builder-DuckDB legible signals into the app tier so FU-076's scorecard can use them
filed 2026-07-22 (42d) · last touch 2026-07-23 (41d) · source FU-076 step-1 implementation (data-model map) · logs 2 · verify yes
FU-076 step 1 (#1742) could only compute deterministic signals already present on the `mcp_server_registry` Postgres row (transport, public-repo, scan-recency, meta coverage). The four highest-value legible signals conduid uses — **maintenance-age** (repo last-commit recency), **scoped-permissions/tool-list**, **tests-present**, **pinned-dependencies** — are NOT in the app tier at all; the data ex
cites: FU-058, FU-075, FU-076

### FU-006 [P2/watch] Architect nvidia-rung non-convergence (~1 in 3 cycles)
filed 2026-07-18 (46d) · last touch 2026-07-24 (40d) · source chairman briefing · logs 7 · verify yes
nvidia rung shows intermittent +0 "did NOT reach propose_directive". Backstops (starvation floor + anchor refill) mask it. Trigger condition: ratio worsens → run a rung eval. ladder_rung_convergence_report is queued as a PHASE 8 spec target.

### FU-049 [P3/open] The mcplookup.app health check never exercises mcplookup.app
filed 2026-07-20 (44d) · last touch 2026-07-24 (40d) · source triage sweep (chairman_briefing_2026-07-20) · logs 1 · verify yes
`mcplookup.app` root returns **301 -> mcprisky.io**, which returns 200. The watch reports 200 and calls the app healthy, but as written it never exercises mcplookup.app's own surface -- it is asserting that the redirect TARGET is up. This was itself one of the five errors the adversarial verification pass caught in the 07-20 briefing draft (see FU-051), i.e. the check misled its own author on the 

### FU-074 [P2/open] Daily queue exhaustion refilled (report-only PHASE 11); the deferred graveyard is auto-growing 5→11/day toward the 40 trigger
filed 2026-07-22 (42d) · last touch 2026-07-24 (40d) · source daily-chairman-review · logs 2 · verify yes
Two findings this run. **(1) The refill (done).** The factory hit `proposed=0 pending=0` at 12:06Z with the runtime current (HEAD `f798ba9e`, 1 commit behind origin/main) — genuine anchor exhaustion after PHASE 10's 7 lanes all merged 2026-07-21, the daily recurrence FU-009/FU-065 predict (burn ~8 targets/day; self-refill `#1644` still unscheduled). Left alone the architect proposed only net-new r
cites: FU-006, FU-009, FU-064, FU-065, FU-069, FU-070, FU-072

### FU-081 [P2/open] Nightly backup runs inline in one MCP PowerShell call, but the ~170s dump outlives the ~60-90s MCP transport timeout — step 4 returns a false TIMEOUT and step 5 can read a manifest-less dir
filed 2026-07-23 (41d) · last touch 2026-07-24 (40d) · source mcplookup-nightly-db-backup · logs 1 · verify yes
This task runs via Windows-MCP PowerShell. `backup_zo_sentinel.py` takes ~170-212s (elapsed_sec 169.5 tonight, 212.3 on 7/22), but the MCP PowerShell transport hard-caps well under that — two calls tonight returned `MCP error -32001: Request timed out` despite 300s/180s tool-level timeouts. Step 4 therefore returned a TIMEOUT **mid-dump**. The detached proxy (`Start-Process`) and the `python` chil
cites: FU-024, FU-025

### FU-086 [P2/open] Nightly backup rides a pre-existing/orphaned flyctl proxy - its own Start-Process proxy silently fails to bind and bind-success is never verified
filed 2026-07-24 (40d) · last touch 2026-07-24 (40d) · source mcplookup-nightly-db-backup · logs 0 · verify yes
Tonight an ORPHANED `flyctl proxy 15432:5432 -a mcplookup-db` (PID 5776, CreationDate 01:30:07, ~1h40m before this run) already held local port 15432. Step 2's `Start-Process flyctl ... proxy 15432:5432` therefore could not bind - it exited silently (never appeared in the flyctl proc list) - and `backup_zo_sentinel.py` connected through the STALE orphan tunnel instead of a fresh one. The backup ha
cites: FU-081

### FU-089 [P2/open] Harden ask-corpus reindex guard + honestly flag unassessed discovery (deferred from FU-088 CofC)
filed 2026-07-24 (40d) · last touch 2026-07-24 (40d) · source daily-chairman-review · logs 0 · verify yes
The 2026-07-24 FU-088 CofC (3 seats + FATHER) shipped only a one-line stopgap (CADENCE_REINDEX_MAX_ROWS 400_000->1_000_000, PR #1781) and explicitly DEFERRED two hardening items to an attended session (schema/consumer blast radius, unsuitable unattended): (1) SIZE-AGNOSTIC GUARD -- replace the absolute-row cost ceiling in cadence_admin_api.py _run_reindex with a guard that trips on per-run DELTA/c
cites: FU-087, FU-088

### FU-098 [P2/open] Size-scaled vast spend guard shipped; wire into run loop + fix resume-from-zero (deferred)
filed 2026-07-24 (40d) · last touch 2026-07-24 (40d) · source daily-chairman-review (chairman-directed) · logs 1 · verify yes
Chairman directed replacing the flat COST_CAP=$3 vast guard with a SIZE-SCALED guard (a flat cap never caught the real bleed -- provisioning churn, e.g. the 2026-07-17 8-wedge cascade -- and would guillotine a big job, stranding partial spend). Shipped tools/rescore/spend_guard.py (PR #1784, CofC 3-seats+FATHER ratified with all amendments): B(N)=clamp(1.5*r*N,$0.50,$10) fixed at export; r=1.83e-5

### FU-092 [P2/open] CVE-axis strengthening queued (family propagation + linker v2) -- autopoietic, loop-set thresholds
filed 2026-07-24 (40d) · last touch 2026-07-24 (40d) · source daily-chairman-review (chairman-directed) · logs 0 · verify NO
With the moat 100% distinct-URL scored (278,026) the 7 LLM axes are dense; the differentiator is the deterministic has_known_cve axis, the sparsest signal we hold. Prod: vuln_advisories=221,885 but vuln_links=613 across 298 servers / 189 canonical_family groups; linker matches only package_exact+repo_exact. Chairman doctrine AUTOPOIESIS: the loop sets/tunes signal thresholds, NO chairman quality-g

### FU-084 [P3/open] Sweep-close the stale-branch RED PR backlog (failing a since-removed `treewalk-smoke` check)
filed 2026-07-23 (41d) · last touch 2026-07-25 (39d) · source triage sweep (chairman_briefing_2026-07-23 §3/§7) · logs 2 · verify yes
The open-PR backlog (19→18) carries a cluster of 7/19–7/21 "build:" PRs all showing exactly RED(1). Root cause diagnosed in the 07-23 briefing: the failing check is `treewalk-smoke`, which **no longer exists on main** (removed from CI), or `capmap-check`/ratchet drift on stale branches — proven to be branch-staleness rather than bad code by a trivial Dependabot bump (#1726) failing the same `treew
cites: FU-013, FU-038, FU-069

### FU-100 [P2/open] weekly_rescore export streams the whole ~463K registry client-side over the Fly proxy (~78 rows/s) before the GPU fires
filed 2026-07-25 (39d) · last touch 2026-07-25 (39d) · source chairman session (probe run 20260725-170556) · logs 2 · verify yes
`ph_export` (tools/rescore/weekly_rescore.py:402) pulls the ENTIRE `mcp_server_registry` (~463,674 rows) to the tower via the fly-proxy DSN (15432->5432) and does distinct-URL representative + never-scored/oldest selection CLIENT-SIDE (lines 416/428/448). Measured this run: steady ~78-80 rows/s over the proxy => ~1.5h of tower-side work BEFORE the fire phase launches the 4090. No GPU spend during 
cites: FU-027, FU-053

### FU-033 [P3/open] Concurrent cadence trigger records a `failed` run instead of `skipped`
filed 2026-07-20 (44d) · last touch 2026-07-26 (38d) · source cadence-jobs-daily-trigger · logs 2 · verify yes
POSTing /api/admin/cadence/perspectives/run-snapshots while a previous snapshot run is still in flight creates a NEW run row that immediately terminates with `status: failed`, `rows_affected: 0`, `detail.error = "advisory lock unavailable (another run in flight)"`. Observed live today: run 31 (10:24:34Z) was still running when run 33 (10:28:15Z) fired; run 31 went on to complete `ok` at 10:35:52Z,

### FU-096 [P2/in-progress] autopoiesis-bar-tracker produces no scoreboard CSV (daily grade is blind)
filed 2026-07-25 (39d) · last touch 2026-07-26 (38d) · source daily-chairman-review · logs 4 · verify NO
The autopoiesis-bar-tracker scheduled task (fires 08:21 local / 12:21Z daily, enabled) has NO lastRunAt in the scheduler listing and no autopoiesis_bar.csv exists on ZoComputer (/home/workspace/) or the tower (D:\zo\Zocomputer Agents\). The task is the doctrine-central daily grade (emission uptake, FU-031 degradation, spineful yield, census vs T1 2026-08-01 / T2 2026-08-08 / T3 2026-08-15). With n
cites: FU-014, FU-036

### FU-064 [P2/watch] Arming the ratchet: what actually shipped, and the three things it deliberately did NOT decide
filed 2026-07-21 (43d) · last touch 2026-07-27 (37d) · source daily-chairman-review · logs 6 · verify NO
Record of the 2026-07-21 CofC ruling and its execution, so the 07-23 review inherits the reasoning rather than re-deriving it. **Evidence that forced the decision:** census at 12:03:33Z read `router_modules_total` 307, `mounted` 31 (flat for weeks), `orphan_count` 276 against a 246 baseline — **+30 in a single day, in observe mode, blocking nothing**. 268 of 276 declare real HTTP routes, all 276 p

### FU-079 [P3/watch] WATCH: track conduid growth, methodology and funding as recurring competitive intel
filed 2026-07-22 (42d) · last touch 2026-07-27 (37d) · source chairman (attended) — conduid competitive assessment · logs 1 · verify NO
conduid is weeks-old and moving; we need a cadence read, not a one-off. Cheap public signals to trend (no login): their live server count via `GET https://conduid.com/api/v2/stats` (are they closing the 46.7K→232K gap, how fast?), `/api/v2/servers` scale, methodology drift (do the 6 signals change; do they add CVE/vuln data like our has_known_cve), `ill-ion` GitHub public repos (`rcpt-protocol`, `
cites: FU-005, FU-054, FU-058, FU-075, FU-076, FU-102

### FU-083 [P2/open] Tower PRIMARY clone carries a ~156-file pre-staged index — `git add <one file>` mis-scopes the commit
filed 2026-07-23 (41d) · last touch 2026-07-27 (37d) · source daily-chairman-review · logs 1 · verify yes
The primary tower clone `D:\zo\zo-sentinel\zo-sentinel` sits on branch `fix/reachability-enforce-cofc-20260721` (tip `c8ed63f`) with a **large pre-existing dirty/staged index — ~156 files, ~18.5K insertions staged**. `git checkout -B <br> origin/main` ABORTS ("Your local changes would be overwritten"), leaving the checkout on the wrong branch; a subsequent `git add PRODUCT_SPEC.md; git commit` the

### FU-114 [P2/open] 4 active services will read HEALTHY in prod while serving nothing
filed 2026-07-27 (37d) · last touch 2026-07-27 (37d) · source prod-drift-sentinel · logs 3 · verify yes
`entity_report_exporter`, `org_api_key_manager`, `overview_dashboard_api` and `verdict_watchlist_service` are registered in `services/active/` and are 4 of the 7 modules prod currently reports as `ModuleNotFoundError` on `/spine/health`. They declare **no router**. The spine logs `4 active service(s) declare no router (skipped)` and does NOT count a skipped service as a failure - so the moment the
cites: FU-102, FU-108, FU-109

### FU-118 [P2/pr-open] The MCP SDK the bridges import was declared in NO requirements file, and directive_mcp did an import-time mkdir on a hardcoded absolute path
filed 2026-07-27 (37d) · last touch 2026-07-27 (37d) · source goose-shadow-research · logs 1 · verify yes
Two coupled defects, both surfaced by the first CI execution of the namespacing probe ([[FU-116]]). **(1) UNDECLARED LOAD-BEARING DEPENDENCY:** `zo_sentinel/mcp_servers/directive_mcp.py` does `from mcp.server.fastmcp import FastMCP`, and `git grep` across every `*.txt`/`*.toml`/`*.cfg` in the repo found **no pin for the `mcp` SDK anywhere** — not in `app/requirements.txt`, not in `tests/ci/require
cites: FU-116, FU-117

### FU-021 [P2/open] rug_pull_monitor: not in go.sh roster; heartbeat cadence is a lie
filed 2026-07-19 (45d) · last touch 2026-07-28 (36d) · source FU-016 diagnosis · logs 3 · verify yes
Two distinct defects behind FU-016's ~740h outage. (a) SURVIVABILITY: rug_pull_monitor is absent from go.sh's 14-daemon roster, so it does not survive a host restart — that is why it stayed down from ~Jul 14. Adding it edits shared launch infra, so it wants owner sign-off. (b) FALSE-STALE: HEARTBEAT_INTERVAL=60 is declared but never used — there is no 60s heartbeat loop; run() heartbeats once per 

### FU-091 [P2/open] Auto-merge did not fire on a clean, green PR (#1744 sat 2 days) - merge automation has no detector for its own non-firing
filed 2026-07-24 (40d) · last touch 2026-07-28 (36d) · source triage sweep (chairman_briefing_2026-07-24 §3) · logs 2 · verify yes
Today's briefing merged #1744 (`build_verdict_metrics_summary_api`) by hand, noting it was "clean, 2 days stuck - auto-merge hadn't fired." A PR that is green and mergeable but on which auto-merge silently never fires is the house "absence has no detector" pattern applied to the merge step: the PR just sits, and if nobody happens to look it feeds both the stale-branch RED backlog (FU-084 - branche
cites: FU-036, FU-074, FU-084

### FU-128 [P3/open] The shared checkout has no ops/host/ scripts on disk — every run re-derives the workaround
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source prod-drift-sentinel (02:05Z) · logs 1 · verify yes
`D:\zo\zo-sentinel\zo-sentinel` sits on branch `verify-manifests`, so `ops/host/verify_candidate.ps1` (and `deploy_prod.ps1`) are not on disk there even though they exist at `origin/main`. `powershell -File .\ops\host\verify_candidate.ps1` fails with "does not exist". The workaround — `git show origin/main:<path> | Set-Content <temp>` then run the temp copy — is documented in `prod_deploy_staged.m

### FU-135 [P2/open] the obvious place to launch a paid rescore from is 48 commits stale
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source moat-rescore-weekly (06:08Z) · logs 2 · verify yes
`D:\zo\zo-sentinel\zo-sentinel` — the canonical tower checkout — sits on branch `verify-manifests`, **48 commits behind origin/main**, with 0 commits of its own not already in main. Its `tools/rescore/weekly_rescore.py` is missing the merged `_billed_dph` fix, so firing from it bills the cost ceiling against the QUOTED offer dph instead of the LIVE instance dph — an 8.4% under-count, i.e. it would

### FU-139 [P2/open] perspective_snapshots runtime is UNBOUNDED — 64min+ with no co-residency and +0.19% data
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source cadence-jobs-daily-trigger · logs 1 · verify yes
Today''s `perspective_snapshots` (run 54, started 2026-07-28T10:23:55Z) was still `running` at 11:28Z — **64 minutes and counting**, vs run 52 yesterday at **11m30s**. That is a **5.6x regression in one day**, and 2.6x worse than the 24.3 min figure that originally opened FU-004. The obvious explanations are all ruled out by today''s own numbers: - **Not data growth.** Registry went 464,531 -> 465

### FU-150 [P2/open] The npm discovery lane has written into a table that does not exist since 2026-06-16 — 3,438 silent failures
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source plan-200k-count-tracker · logs 1 · verify yes
While diagnosing a (false) registry stall I read the discovery daemons' logs and found a real one underneath. `discovery_npm_paginator` is alive, healthy and productive-looking — every 30 minutes it logs `cycle done ... seen=100 kept=100 written=0 errors=0`. **`kept=100 written=0 errors=0`**: it selects a hundred candidates, writes none, and reports zero errors. The write is failing one line earli

### FU-152 [P2/open] Four copies of goose_runner.py on the box, and the one every deploy-check instinct points at is a DECOY
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source follow-up-triage (found while verifying #2177 reached its runtime) · logs 1 · verify yes
**I got this wrong myself, in this run, and the wrong answer was extremely convincing — which is the entire point of filing it.** Verifying whether #2177 (the FU-031 PYTHONPATH fix) had reached the live builder, I searched for `goose_runner.py`, read the copy under the runtime clone `/home/workspace/zo/zo-sentinel/zo-sentinel/`, found **zero** `PYTHONPATH` occurrences and a mtime of Jul 27 12:08 —
cites: FU-099, FU-102, FU-138, FU-141, FU-142, FU-149, FU-150, FU-151

### FU-153 [P2/pr-open] The FU-138 fix was written to a path outside the repo, so the next run could not find it
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source prod-drift-sentinel (14:00Z run) · logs 1 · verify yes
The previous run diagnosed [[FU-138]] correctly — the orphan worktree `D:\zo\_prod_dryrun` that #2173 taught `verify_candidate.ps1` to HEAL is **manufactured fresh on every slow run** by launching the verifier in the FOREGROUND from an MCP shell whose request timeout is shorter than the gate suite. It wrote the remedy, `run_verify.ps1`, and filed FU-138 `resolved`. **The remedy was written to `D:\
cites: FU-138

### FU-154 [P2/pr-open] The copilot-autofix workflow has startup-failed 1,919 times and has never once run
filed 2026-07-28 (36d) · last touch 2026-07-28 (36d) · source prod-drift-sentinel (14:00Z run, found while gating candidate `7fc39201`) · logs 1 · verify yes
`.github/workflows/copilot-autofix-commit.yml` (added 2026-06-25, #687 "ci: auto-commit Copilot Autofix suggestions (zero-click)") declares `on: code_scanning_alert: types: [appeared_in_branch]`. **`code_scanning_alert` is a GitHub *webhook* event; it is not a valid GitHub Actions workflow trigger.** GitHub cannot parse the file, so instead of firing on alerts it emits a **startup_failure run on e
cites: FU-025, FU-031, FU-064, FU-114, FU-153

### FU-160 [P2/open] 171 goose entrypoint copies on ZoComputer; 3 are live, and the raw-hash/mtime test for "which one runs" is WRONG
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source governance-lane (chairman-directed) · logs 2 · verify yes
Chairman: *"mark the decoys for removal or depreciation - there are multiple gooses running in zocomputer."* Inventory: **171** files matching the two goose entrypoints. **3 are live**, resolved from /proc and not from any path: (a) builder-goose `/home/workspace/zo_sentinel/goose_runner.py` (pid 6907); (b) architect-goose `/home/workspace/zo_sentinel/sentinel_directive_generator_goose.py` (pid 85
cites: FU-025, FU-064, FU-161

### FU-161 [P2/open] `fire_gate` named the 100th commit as the target head, and would have accepted a truncated 300-file compare as a complete image surface
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source prod-drift-sentinel · logs 7 · verify yes
`tools/fire_gate.py` is the instrument the chairman is told to run immediately before firing prod. Its `changed_files()` derived the target head from `(doc["commits"])[-1]["sha"]` of the **first** JSON document returned by `gh api .../compare/... --paginate`. Compare pages commits **100 at a time**, so every delta over 100 commits reported the 100th commit as the head. Measured live 04:4xZ on the 

### FU-164 [P2/open] A sentinel run ran all eight gates and left NO record, and nothing reconciled evidence against state
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source prod-drift-sentinel · logs 3 · verify yes
`prod-drift-sentinel` writes its only durable record — `prod_deploy_state.json` — at the **end** of a run, while `verify_candidate.ps1` rescues its verdict artifact in the **middle**. Measured this run: `_deploy_evidence/verdict_7fc39201_20260729T075153Z.json` carries `checked_utc: 2026-07-29T07:51:53Z`, while `prod_deploy_state.json`'s `last_check_utc` still read **2026-07-29T05:01:00Z** at 10:49

### FU-169 [P2/open] a directory listing is not a queue depth
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source daily-chairman-review · logs 1 · verify yes
`ls directives/proposed | wc -l` returns **157**; the number of files the queue can actually read (`*.json`) is **0**. The 157 is 137 `.expanded` + 12 `.rejected` + 4 `.duplicate` + retired debris. Any briefing publishing "proposed=157" reports a healthy backlog while the builder starves. Same shape as `.bak` files masking `pending/` (FU-011, already fixed inside `_count_proposed`) — but the FIX l

### FU-171 [P3/open] 15 spent one-shot tasks still fire, and they inflate every adoption denominator
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source follow-up-triage (sweep of GOVERNANCE_ROLLOUT.md, 07-28 20:30, previously unswept) · logs 2 · verify yes
`GOVERNANCE_ROLLOUT.md` §2 counts 33 task files in the 2026-07-28 snapshot and finds they are not one population: **17 recurring organs** (the real denominator) and **15 spent one-shots** -- probes and wedge-checks that already answered and still fire. Named: `eval-watch-v2c` (last touched **2026-05-07, 82 days**), `score-wave-check-2`/`-3`, `fu104-monitor-run-*`, `vast-45168912-wedge-check`, `rei
cites: FU-101, FU-140

### FU-178 [P3/watch] release pinning + a real upgrade cycle -- the work rollback is actually waiting on
filed 2026-07-29 (35d) · last touch 2026-07-29 (35d) · source follow-up-triage (chairman ruling) · logs 2 · verify yes
**Chairman ruling 2026-07-29:** for an MVP app that is still malleable, a rehearsed rollback is the wrong investment; rollback becomes meaningful once releases are PINNED to a version and there is a proper upgrade cycle to roll back *within*. Upheld and folded into `cofc_2026-07-29_amendment_phase2_gate.md` §4.6, which replaced capability C3 (rehearsed rollback) with C3 (recovery demonstrated). **
cites: FU-082, FU-114

### FU-069 [P2/open] Dedup keys on filename, so the same endpoint gets built three, four and five times
filed 2026-07-21 (43d) · last touch 2026-07-30 (34d) · source triage sweep (INTEGRATION_SURFACE_STRATEGY_2026-07-20 + 07-21 briefing) · logs 2 · verify yes
Split out of FU-008, whose trigger is specifically a same-NAME pair and remains unfired. This is the sibling defect: `already_built_modules` dedups on **basename**, so two directives with different filenames serving the *same URL* both build. Quantified twice, independently. From the census (`INTEGRATION_SURFACE_STRATEGY_2026-07-20.md` §1): `GET /servers/compare` built **4×**, `GET /servers/{id}/v
cites: FU-120

### FU-085 [P2/open] Builder's goose session store corrupts and silently blocks EVERY build — no detector; the architect store is degrading the same way
filed 2026-07-23 (41d) · last touch 2026-07-30 (34d) · source attended drift-close (safe_ff exposed it) · logs 3 · verify yes
Closing the FU-028 container drift today (attended `safe_ff.sh` → runtime HEAD `23ac8a7`) refilled the builder queue, and the first real build attempt (`build_verified_cves_api`, 14:19Z) failed goose-1.43 at RECIPE LOAD with `Error: error returned from database: (code: 11) database disk image is malformed` — every subsequent cycle failed identically, so the builder was **hard-down on the build sid
cites: FU-017, FU-036, FU-120, FU-152

### FU-103 [P2/in-progress] Tie the ledger ⇄ MCP memory ⇄ graphify KL into one FU-keyed context graph
filed 2026-07-25 (39d) · last touch 2026-07-30 (34d) · source chairman (Robin, direct) — design question · logs 4 · verify yes
Chairman: *"can the ledger and MEM MCP be tied together such that the graphify KL relevant to the FU can be accessed for richer, faster, more token-efficient understanding?"* Current state (measured): THREE stores, keyed differently, only half-connected. (a) **Ledger** `FOLLOWUPS.md` — flat markdown, already uses `[[wiki-links]]` to scars + names files/modules/PRs, but is **NOT indexed by MEM** (m

### FU-200 [P2/open] The casing-repair series was never cumulative: goose_runner.log's coverage window moved under a 5-day trend line
filed 2026-07-30 (34d) · last touch 2026-08-01 (32d) · source autopoiesis-bar-tracker (run 2, 14:50-15:00Z) · logs 5 · verify yes
autopoiesis_bar.csv has tracked `casing_repairs_24h` as a CUMULATIVE `grep -c "casing-repair" /home/workspace/logs/goose_runner.log` and trended it 58 -> 124 -> 192 -> 321 (7/29) -> 218 (7/30 12:23Z) -> 305 (7/30 14:52Z). A cumulative counter cannot fall. It fell by 103 inside a single day, so the FILE changed, not the behaviour: `head -1` on the live log is `[2026-07-29T08:37:38Z]`, and a per-day
cites: FU-196

### FU-218 [P2/open] The link auditor written to stop namespace-conflation from manufacturing false reds was itself omitting a namespace, and 6 of its 9 reds were its own blind spot
filed 2026-08-01 (32d) · last touch 2026-08-01 (32d) · source prod-drift-sentinel 04:45Z slot, closing an honest gap the 00:47Z slot recorded against itself · logs 5 · verify yes
`D:\zo\Zocomputer Agents\_tools\kl_link_audit.py` proves the Graphify join has no dangling edges. Its own docstring states the rule twice, with two dated measurements behind it: **"Resolve a link against EVERY store before calling it broken, or do not run the check."** It then resolved against three stores -- the ledger, SPACES memory, MCP memory -- and **not** against the store where the most-cit

### FU-125 [P2/open] FOLLOWUPS.md has grown 17x in 7 days and is outgrowing the context that reads it — the ledger governing autopoiesis is becoming unreadable
filed 2026-07-27 (37d) · last touch 2026-08-02 (31d) · source chairman (attended) — "is there cascading risk / baseline what is nominal for autopoiesis" · logs 1 · verify yes
Raised as a governance question and it turned up a real, *measurable* cascading risk — one that is not about any single write but about the aggregate of a nominal action repeated. Growth straight from the backups in the working folder: `bak_20260720` **35,488 bytes / 24 FU / 4,980 words** → `bak_20260726_pw` 432,045 / 108 / 61,919 → `bak_20260727_final` 590,620 / 124 / 84,565 → **current 600,769 b
cites: FU-064, FU-079, FU-102, FU-110, FU-226

### FU-142 [P2/open] 44 staged self-tests fail as "relative import with no known parent package" -- a package run as a script
filed 2026-07-28 (36d) · last touch 2026-08-02 (31d) · source daily-chairman-review · logs 5 · verify yes
Found by the FU-031 blast-radius probe (381 staged self-test-bearing modules run read-only under the exact harness env WITH the PYTHONPATH fix applied). The single largest failure bucket is **44x `ImportError: attempted relative import with no known parent package`** -- larger than the `Orgs` model-naming bucket (38). These are NOT module defects. `_selftest_gate` invokes `subprocess.run([sys.exec
cites: FU-031, FU-071, FU-231

### FU-193 [P2/open] Five open P1s carry `verify: NONE`, and the task-snapshot guard detector now emits 13 known-false alarms every run
filed 2026-07-30 (34d) · last touch 2026-08-02 (31d) · source daily-chairman-review · logs 5 · verify yes
Two hygiene defects with the same failure mode -- a signal that decays into noise. (a) `ledger_lint.py` rc=1 with five **E7** findings: FU-031, FU-101, FU-117, FU-119, FU-167 are open P1 defects whose acceptance test is not stated as a runnable command, plus one **E9** (FU-158, verify contains the forbidden token `dd`). A P1 that no surface can query is a coin flip. (b) `_tools/snapshot_scheduled_
cites: FU-031, FU-101, FU-114, FU-117, FU-119, FU-167, FU-197

### FU-194 [P2/open] A sibling's prompt condensation silently dropped three hard-won R5 measurement-basis lines from autopoiesis-bar-tracker
filed 2026-07-30 (34d) · last touch 2026-08-02 (31d) · source daily-chairman-review · logs 3 · verify yes
Diffing task snapshots 20260729T122447Z -> 20260730T121624Z shows `autopoiesis-bar-tracker` was condensed (-1338 bytes / -6 lines) and lost, besides the HARNESS_DOCTRINE pointer: (1) the casing-repair note "BASIS: CUMULATIVE log total, NOT 24h -- report the total AND the delta", (2) the redirects note "parse per-day by its `ts` field, do not just `wc -l`; a partial-day sample is not the day", (3) 

### FU-172 [P2/open] five ledger entries parsed with NO status, so the verifier could not see them -- and nothing flagged it
filed 2026-07-29 (35d) · last touch 2026-08-03 (30d) · source follow-up-triage · logs 6 · verify yes
`ledger_lint --stats` reported `by status: (unset)=5`. `fu_ledger.parse()` resolved `status`, `priority` and `source` **only** from the `- date:` line; the five entries filed that morning ([[FU-165]]..[[FU-169]]) wrote them as their own `- status:` / `- priority:` keys instead. Those five therefore parsed with `status == ""`, so `is_open()` returned False and `fu_verify.py` would not act on them -
cites: FU-165, FU-166, FU-169, FU-174, FU-175, FU-212, FU-233

### FU-030 [P2/open] Two consecutive days of exactly zero score-row delta
filed 2026-07-20 (44d) · last touch 2026-08-04 (29d) · source zo-sentinel-pipeline-watch · logs 7 · verify yes
/freshness on 7/20 returns scores_rows=1,206,065 and scored_servers=172,295 — byte-identical to the 7/19 reading — while registry_rows grew 232,180 -> 232,188 and never_scored grew 59,885 -> 59,893. So new registry rows arrive and nothing scores them. The 7/19 watch raised the adjacent question ("ScoreWave2's 65K refresh leg produced ZERO row delta — verify run 20260719-003024 actually imported") 

### FU-244 [P2/open] The test the charter orders every harness change to run did not exist -- and an empty result set is indistinguishable from a broken psql
filed 2026-08-04 (29d) · last touch 2026-08-04 (29d) · source mcplookup-nightly-db-backup · logs 2 · verify yes
**THE NAMED TEST WAS GONE.** The SKILL tells every run: after ANY change under `db_backups/`, run `_verify_fixes.py (6, LIVE -- it plants orphans and holds a real session open against the scratch DB)`. On 2026-08-04 **that file did not exist on this box** (recursive search of `D:\zo` and `C:\Users\robin\OneDrive\Documents` found nothing). Its 07-27 output log DID survive at `db_backups/logs/verify

### FU-162 [P3/open] 529 runtime `.done.json` sentinels are TRACKED in git, so every deploy's auto-stash RESURRECTS the 39 the runtime deleted
filed 2026-07-29 (35d) · last touch 2026-08-05 (28d) · source deploy-runtime-from-main · logs 4 · verify yes
`directives/<id>.done.json` is a **terminal runtime sentinel** — `goose_runner.is_goose_eligible()` and `proposed_to_pending_promoter` both treat its existence as "finished with this directive, skip it". It is runtime state, but **529 of them are committed to the repo**, and `.gitignore` covers only the `directives/done/` and `directives/failed/` *directories* (lines 7–8), not the top-level `direc
cites: FU-019, FU-067, FU-073, FU-085, FU-160, FU-163

### FU-188 [P3/open] The watchdog's restart branch cannot see a supervisor that already exists, so a crash-loop is amplified into a supervisor leak
filed 2026-07-30 (34d) · last touch 2026-08-05 (28d) · source deploy-runtime-from-main (09:10Z slot) · logs 2 · verify yes
`watchdog.sh::_daemon_tp` decides a trust-pipeline daemon is down with `pgrep -c -f "python.*$script" == 0`, and on 0 spawns a fresh `daemon_wrapper.sh`. **The count deliberately excludes wrapper processes** -- v3.4 anchored the pattern to `python.*<script>` because v3.3 was mass-deduping all 10 daemons every 15min by counting the wrapper (which carries the script path as an argv) as a duplicate. 
cites: FU-186, FU-187

### FU-261 [P2/open] A `pgrep` predicate sent across the zo_call bridge COUNTS ITS OWN TRANSPORT, so every process-liveness probe reads exactly one too many
filed 2026-08-05 (28d) · last touch 2026-08-05 (28d) · source follow-up-triage · logs 1 · verify yes
`pgrep -fc 'daemon_wrapper.sh threat_intel_ingestor'` executed through `zo_call.py` returns **2** while the runtime holds exactly **ONE** such supervisor. `zo_call.py` ships the command to the runtime inside a `sh -c ... <<'__ZO_EOF_<hex>__'` heredoc, so the search pattern is present verbatim in the TRANSPORT's own command line and `pgrep -f` matches it. The inflation is exactly +1, it is silent, 
cites: FU-115, FU-255

### FU-274 [P2/open] `rule_echo._live_occurrences` uses a bare +/-220 char window, so a citation can silence a live restatement two lines away
filed 2026-08-06 (27d) · last touch 2026-08-06 (27d) · source follow-up-triage · logs 0 · verify yes
While fixing [[FU-270]] this lane ported `rule_echo._live_occurrences` into `snapshot_scheduled_tasks.ps1` and its own six-pole self-test FAILED the port: a document holding a CITATION of a retired rule and, two lines later, a LIVE restatement of the same phrase scored **0 live occurrences** where the true answer is 1. The citation's `retired` marker sits inside the +/-220 window of the live one, 
cites: FU-270

### FU-252 [P2/open] CHAIRMAN RULING: the emergency hatch grades an ABORTED deploy as a FALSE EMERGENCY, so the counter that keeps the hatch honest punishes the one case where nothing was spent
filed 2026-08-04 (29d) · last touch 2026-08-07 (26d) · source chairman, attended session, answering the grading question raised by [[FU-234]] · logs 2 · verify yes
**THE RULING, IN THE CHAIRMAN'S ANSWER: grade on (predicate RED) AND (the act was actually performed).** Recorded here because it was given in session and a decision that lives only in a transcript is not a decision ([[FU-229]]). **THE MOTIVATING INCIDENT IS [[FU-234]] AND IT IS NOT HYPOTHETICAL.** On 08-02 an emergency change was authorised (`authority.py` rc=0, predicate measured RED by the tool
cites: FU-229, FU-234, FU-235, FU-247

### FU-282 [P2/open] A SECOND peer_review decision store exists, holds ZERO decisions, and is invisible to every search because its directory name is not encodable (was FU-276 -- number collision, renumbered 2026-08-07 by follow-up-triage)
filed 2026-08-07 (26d) · last touch 2026-08-07 (26d) · source graphify-kl-daily-refresh · logs 3 · verify yes
There are TWO `peer_decisions.json` files under the connected folder. The live one is `D:\zo\Zocomputer Agents\peer_decisions.json` (37,571 bytes, 8 decisions, last written 2026-08-07T10:10Z by this lane's falsification). The other is 202 bytes, holds **ZERO decisions**, was written 2026-08-06T08:12Z, and sits in a subdirectory whose real name is `D` + U+F03A + `zo` + U+F05C + `Zocomputer Agents` 
cites: FU-265, FU-276

### FU-279 [Punspecified/open] FAMILY A REGRESSED FROM 0 TO 6 SITES, and only the explicit lookup could see it -- it never entered any top-N list
filed 2026-08-07 (26d) · last touch 2026-08-07 (26d) · source autopoiesis-bar-tracker · logs 0 · verify yes
Family A (casing / plural-prefix drift onto a model that REALLY EXISTS) was closed at 133 sites (`e031cf6f` / PR #2701) and read 0 since; on 2026-08-07 it reads 6 -- `VulnerabilityAdvisory` x5 and `VulnerabilityLink` x1, whose renamed referents `VulnAdvisory` (app/models.py:169) and `VulnLink` (:189) both exist. Both counts sit far below the x39 leader, so the regression entered NO top-N list and 

### FU-280 [Punspecified/open] the `--enforce` halt has outlived its condition -- DECIDE_AND_LOG is available, and condition 5 (NOT THE FILER) is why this lane must not self-clear it
filed 2026-08-07 (26d) · last touch 2026-08-07 (26d) · source autopoiesis-bar-tracker · logs 2 · verify yes
The ESCALATE-ONLY hold on `promote_staged_to_active.py --enforce --max-per-run 1` was justified by a condition (31 of 60 cohort files missing from git) that is now FALSE: 0 missing / 14 of 14 fully tracked on origin/main. Routed to peer review rather than self-cleared, correctly, under condition 5 (NOT THE FILER). The peer proposal `enforce-first-cohort-max-per-run-1` was FALSIFIED 2026-08-07 by f

### FU-057 [P2/open] A leaked flyctl proxy holds an open tunnel to prod PG, and the nightly's own liveness check cannot tell whose tunnel it is
filed 2026-07-21 (43d) · last touch 2026-08-09 (24d) · source mcplookup-nightly-db-backup · logs 17 · verify yes
Tonight's run started its proxy, saw `Test-NetConnection localhost:15432 -> True`, and proceeded. That check was **a false positive**: port 15432 was already bound by a DIFFERENT, orphaned flyctl proxy. Captured live from `Win32_Process`: PID **3496**, command line `flyctl proxy 15432:5432 -a mcplookup-db`, **CreationDate 2026-07-21 01:30:09** -- roughly 100 minutes before this run started its own
cites: FU-054, FU-149

### FU-177 [P2/open] the Phase 2 gate measures attended fires, which never exercise the thing Phase 2 would add
filed 2026-07-29 (35d) · last touch 2026-08-09 (24d) · source follow-up-triage (chairman-directed) · logs 6 · verify yes
The 2026-07-25 CofC 3+FATHER ruling gates Phase 2 auto-fire behind `>=5 clean staged->fired deploys`. **Five ATTENDED fires exercise `ops/host/deploy_prod.ps1`, which is byte-identical whether a human or the task invokes it.** They never exercise the only thing Phase 2 adds: the task's own DECISION to fire and its reading of `accept_gate`'s exit code. So the counter accrues confidence in the artif
cites: FU-096, FU-168, FU-178

### FU-284 [Punspecified/open] one file, one word, two thresholds: the trajectory header printed "silent_lanes 0 FLAT" on the run that ranked a silent lane at 85
filed 2026-08-07 (26d) · last touch 2026-08-09 (24d) · source improvement-loop (cycle-0019) · logs 2 · verify yes
`improve_loop.candidates()` flags a lane silent at >36h; `improve_loop.surface_census()` flags it at >48h (`timedelta(days=2)`); `lane_start.STALE_H` is 36. On 2026-08-07 the same `--select` stdout therefore printed `silent_lanes 0 -> 0 FLAT` in its header and `[85] silent_lane improvement-loop -- 1 lane(s) have not checked in for >36h` twelve lines lower. The 2026-08-06 repair of this same code f

### FU-299 [Punspecified/open] A MODULE-SCOPE CHANGE IN MY OWN CLASSIFIER WOULD HAVE PUBLISHED A 21-SITE IMPROVEMENT THAT DID NOT HAPPEN
filed 2026-08-09 (24d) · last touch 2026-08-09 (24d) · source autopoiesis-bar-tracker · logs 8 · verify yes
Family B was about to be published as 125 sites / 43 distinct, a 21-site fall that reads as progress. The fall was a scope change in the lane's own classifier (app.models only, where 08-08 also swept app.db). On the 08-08 basis the figure is 146 / 46 -- site count IDENTICAL to yesterday.

### FU-300 [Punspecified/open] EIGHT OF TEN FALSIFICATIONS NEVER RAN A CONTROL, AND THE FLAG THAT WOULD HAVE SAID SO IS TRUE BY CONSTRUCTION
filed 2026-08-09 (24d) · last touch 2026-08-09 (24d) · source autopoiesis-bar-tracker (adjudicating a proposal by graphify-kl-daily-refresh) · logs 8 · verify yes
_tools/peer_review.py computes discrimination_proven = bool(broke or pc_rc == 0). On a FALSIFIED row `broke` is True, so the flag is True by construction and audit()'s only discrimination reading can never fire on a falsification. Live store 2026-08-09T14:40Z: 16 falsification records, 10 with broke_it=True, 0 with a falsy flag, 8 of those 10 with positive_control_rc null -- no control was ever ex

### FU-301 [Punspecified/open] A REVERT_CHECK THAT RUNS ON HOST A CANNOT DEMONSTRATE REVERSIBILITY OF AN ACTION ON HOST B
filed 2026-08-09 (24d) · last touch 2026-08-09 (24d) · source autopoiesis-bar-tracker · logs 8 · verify yes
peer_review.py runs tower-side (Windows); the promotion it adjudicates happens in a git worktree on the zo box the tower has no path to. Every revert_check written for it was therefore destined to be an assertion -- the cause under three days of falsifications of enforce-first-cohort-max-per-run-1 (v1 08-07, v2 08-08).

### FU-034 [P3/open] Send the #10348 shell-timeout finding upstream to aaif-goose/goose
filed 2026-07-20 (44d) · last touch 2026-08-10 (23d) · source goose-shadow-research · logs 1 · verify yes
FU-017's canary produced a finding that is upstream-shaped and currently lives only in our private ledger: in goose v1.43, per-extension `timeout:` in recipes/config is **NOT** honoured for the builtin developer-shell kill. Resolution order read from source (crates/goose/src/agents/platform_extensions/developer/shell.rs, the #10348 diff) is per-call `timeout_secs` → `GOOSE_DEFAULT_EXTENSION_TIMEOU

### FU-055 [P2/open] Scoring is not stalled — it is SATURATED; the 200K metric measures the wrong thing
filed 2026-07-20 (44d) · last touch 2026-08-10 (23d) · source chairman "lets get scoring running" session · logs 4 · verify yes
FU-030 has been asking for three days why scored_servers is flat, on the premise that a no-op import leg is dropping rows. Prod says otherwise: `max(scored_at)` = **2026-07-19T05:17:31** and **1,197,665 of the 1,206,065 total score rows were written in the last 5 days** — i.e. ScoreWave scored essentially the entire corpus and then finished. Nothing is stuck; there is simply nothing queued. The re
cites: FU-053, FU-087, FU-090

### FU-062 [P2/open] drift-check's "cheap inline" precheck takes 38s idle and blocks the trigger POST past the caller's timeout
filed 2026-07-21 (43d) · last touch 2026-08-10 (23d) · source cadence-jobs-daily-trigger · logs 7 · verify yes
`POST /api/admin/cadence/ask/drift-check` did not return to this task at all today — the call was abandoned at the client's transport ceiling — while the run it had created (run 36) went on to complete `ok` 13.3 min later. **The endpoint is NOT the problem I first assumed, and the correction is the useful part of this entry.** I initially wrote this up as "drift-check runs the reindex synchronousl
cites: FU-004, FU-027, FU-033, FU-053

### FU-295 [P2/open] The recurrence fix landed in the WRITER and the defect lives in the 14 PROMPTS that call it -- 13 of 14 lanes are invoking the unkeyed form the fleet documents
filed None (Noned) · last touch 2026-08-10 (23d) · source unspecified · logs 9 · verify yes
**THE ROW THAT PROVES IT IS MY OWN, AND record() BEHAVED PERFECTLY.** I hit `ps-command-nested-quotes` twice tonight (PowerShell strips embedded double quotes from a native-command argument, so `flyctl ssh -C` re-split the remote command: `unknown shorthand flag: '4' in -40;`, then `malformed resolve command` once a `python -c` was nested inside). `record()` printed the UNKEYED warning AND the ful
cites: FU-195, FU-201, FU-264, FU-271

### FU-308 [P2/open] The retired-prose detector enumerates SKILL files only, so an approval gate living in a LEDGER is invisible to it
filed 2026-08-10 (23d) · last touch 2026-08-10 (23d) · source goose-shadow-research · logs 1 · verify yes
`authority.py --retired-prose` exists to find approval gates that outlived the rule that created them. Its implementation is `retired_prose_live(skill_dir: Path | None = None, ...)` (authority.py:573) and it walks **SKILL files**. Today `--show` printed `RETIRED PROSE STILL LIVE IN A SKILL: none` — correctly, and uselessly, because the retired prose was not in a SKILL. `GOOSE_WATCH.md`'s "Queued d

### FU-320 [P3/open] A near-miss clause NAME is priced as a wall, and during the away window that is an FU round-trip for a typo
filed 2026-08-11 (22d) · last touch 2026-08-11 (22d) · source moat-rescore-weekly · logs 4 · verify yes
`authority.py --may paid_gpu_wave` returns **BLOCKED / UNCLASSIFIED / DECIDE_AND_LOG** while the clause that grants exactly that act, `paid_gpu_scoring_waves`, sits one word away in the same `authority.json`. The refusal is textually identical to the refusal for a genuinely unnamed action, so the caller cannot tell a typo from a real wall. Inside the away window the prescribed remedy for UNCLASSIF
cites: FU-265

### FU-322 [P2/pr-open] The forensics log could not tell a transient network blip from an expired credential
filed 2026-08-11 (22d) · last touch 2026-08-11 (22d) · source moat-rescore-weekly · logs 2 · verify yes
`vast_score_onstart.sh` ran `git clone ... >/dev/null 2>&1 || fail "clone"` and `git fetch --depth 1 origin "$SCORE_BRANCH" >/dev/null 2>&1 || fail "fetch bundle"`. When the fetch failed, the ENTIRE forensics artifact I3 exists to preserve was **356 bytes** ending `FATAL: fetch bundle` -- with git's own explanation discarded one character earlier. From that log a dead PAT, a missing branch, a corr
cites: FU-319

### FU-325 [P2/open] A task prompt grants, in prose, an action authority.json holds forever
filed 2026-08-11 (22d) · last touch 2026-08-11 (22d) · source mcplookup-nightly-db-backup · logs 0 · verify yes
Step 7 of this lane's SKILL says of aged backups: *"Delete the listed dirs yourself only if step 5 PASSED"* -- an unconditional grant to delete, gated only on the backup having succeeded. `authority.json` classes `data_deletion` as FOREVER_HELD, not peer-clearable, "no rollback exists"; `authority.py --may data_deletion` returned BLOCKED this run. The envelope resolves the contradiction (authority

### FU-327 [P2/open] A cost_breach teardown is forensics-blind by construction, so I3 cannot be satisfied on that path
filed 2026-08-11 (22d) · last touch 2026-08-11 (22d) · source moat-rescore-weekly · logs 1 · verify yes
I3 requires forensics pulled BEFORE any destroy, *success or fail*. Wave `20260811-063956` was destroyed on `cost_breach` with **`collected: []`** and no `results/` directory at all. That is not a violation of I3 by the harness -- it is I3 being **unsatisfiable on this path by construction**. The only retrieval mechanism is a git branch the POD pushes, and the pod pushes exactly twice: on success 
cites: FU-322

### FU-330 [P2/open] the anti-orphan constructor could report NEVER LAUNCHED for a perfectly healthy launch, because it gave two different questions the same clock
filed 2026-08-11 (22d) · last touch 2026-08-11 (22d) · source improvement-loop · logs 3 · verify yes
`self_detach()` waits for the child's `.rc`, and if the caller's `wait` elapses it distinguishes **STILL RUNNING (3)** from **NEVER LAUNCHED (2)** by whether the wrapper wrote its `.started` sentinel. It used the caller's `wait` as the deadline for BOTH questions. **`.started` is written asynchronously by the launcher**, so with `--detach-wait 0` the parent asked "did it ever start?" ~200ms before

### FU-316 [P2/open] BOTH friction RUNNERS HAND THE LINE TO cmd.exe, AND NOTHING SAID SO -- A POWERSHELL PROLOGUE BECAME A LAUNCH THAT LOOKED LIKE A COMPLETED RUN
filed 2026-08-10 (23d) · last touch 2026-08-12 (21d) · source follow-up-triage--implement-agent-for-the-zo-sentinel-project · logs 3 · verify yes
`friction.detached()` writes its command line VERBATIM into a `.cmd` and runs it under `cmd /c`; `friction.run()` uses `subprocess.Popen(cmd, shell=True)`, which on Windows is also cmd.exe. Neither says so where a caller would see it. A lane that has spent the session at an MCP **PowerShell** prompt writes the prologue its fingers know -- `cd "D:\..."; $env:VAR="v"; python ...` -- and hands it str
cites: FU-339

### FU-340 [P2/open] a PROPOSED peer decision carries a verify_cmd that measures a DIFFERENT artifact than its action changes
filed 2026-08-12 (21d) · last touch 2026-08-12 (21d) · source follow-up-triage--implement-agent-for-the-zo-sentinel-project · logs 3 · verify yes
`scope-recurring-friction-predicate` (PROPOSED by `improvement-loop` 2026-08-12T12:40:12Z, clause `redefining_the_metric`, adversary UNASSIGNED) proposes to rescope the improve_loop grading window for candidate kind `recurring_friction` from `friction.py --recurred <sig> --days 7 --min 3` to `--since <cycle.opened_at> --min 1`. Its `verify_cmd` is `python "D:\zo\Zocomputer Agents\_tools\probe_aggr

### FU-287 [Punspecified/open] zo-sentinel-pipeline-watch carries the lane_start line and skipped it two days running
filed 2026-08-08 (25d) · last touch 2026-08-13 (20d) · source unspecified · logs 6 · verify yes
zo-sentinel-pipeline-watch ran at 08:05Z today, carries the lane_start line at SKILL.md:107, and has left no receipt for 51.9h -- it holds the instruction and does not execute it **measured:** 2026-08-08T12:0xZ. **BASIS:** scheduler `lastRunAt` 2026-08-08T08:05:53Z vs `lane_receipts.json` `zo-sentinel-pipeline-watch.at` = 51.9h ago. **The finding.** The lane RAN today. Its SKILL.md **does** name `
cites: FU-283, FU-284, FU-286

### FU-348 [Punspecified/open] `tee-floods-mcp-result` was armed on the WRITE half; 4 of its 5 bites came through the READ
filed 2026-08-13 (20d) · last touch 2026-08-13 (20d) · source unspecified · logs 1 · verify yes
found by `improvement-loop` cycle-0046, floor GREEN (14/14) before selection. The `tee-floods-mcp-result` guard was armed on the WRITE half only; 4 of its 5 ledgered bites came through the READ. Fixed in CODE same day: `_tools/friction.py` `_unbounded_artifact_read()` (ARM 2, keyed on absence of a bound) + `grep_bounded()` constructor + `read_out()` decoder unification + five two-pole negative con

### FU-237 [P2/open] The freshness SLA and the scoring cadence are BOTH 7 days, so `newest_scored_at` hits the breach threshold in the same minute the next wave lands -- the flag is decided by import-lag noise, not by health
filed 2026-08-03 (30d) · last touch 2026-08-18 (15d) · source plan-200k-count-tracker 15:35Z · logs 8 · verify yes
**MEASURED, WITH THE BASIS (R5).** `GET https://mcprisky.io/freshness` at `computed_at 2026-08-03T15:35:04Z`, `cache_age_seconds 0.0` (so not a stale aggregate): registry_rows **471,734** · scored_servers **280,811** · never_scored **190,923** · scores_rows **1,965,677** · newest_scored_at **2026-07-30T01:14:52Z**. `scored_servers` has been flat at 280,811 for four consecutive days (07-30 -> 08-03
cites: FU-090, FU-207, FU-326, FU-327

### FU-347 [P2/open] a lane can RUN daily and still not check in -- the scheduler proves liveness, and liveness is not obligation
filed 2026-08-13 (20d) · last touch 2026-08-23 (10d) · source follow-up-triage (shepherd sweep) · logs 3 · verify yes
`lane_start.py` reported `zo-sentinel-pipeline-watch` silent for 57h (window 36h) and `loop_health.py` lists it among 3 lanes recording no friction at all. But the live scheduler shows it RAN: `lastRunAt 2026-08-13T08:06:11Z`, on cron `0 4 * * *`, enabled. It ran on 08-12 and 08-13 and checked in on neither. Its SKILL.md DOES carry the instruction, verbatim, at line 107. so the two readings measur

### FU-355 [P2/open] tools/reload_daemon.sh cold-relaunch passes '-m' as a script path and cannot revive a daemon it just killed
filed 2026-08-31 (2d) · last touch 2026-08-31 (2d) · source follow-up-triage (found live during FU-353 arming) · logs 0 · verify yes
On the zo runtime, `tools/reload_daemon.sh autopoiesis_bar_tracker` (a) reported "killing python child (old pid none)" while a pre-patch child (pid 41021, started 04:13Z) was alive and was left orphaned on in-memory code — the FU-349 shape in the repair tool itself, same family as restart_promoter.sh reporting a restart it did not perform; and (b) its cold-relaunch branch failed with `[wrapper] ER

### FU-356 [P2/in-progress] inline-interpreter-source had no stdin-to-DETACH entry; --pysrc --detach added (cycle-0056)
filed 2026-08-31 (2d) · last touch 2026-08-31 (2d) · source improvement-loop · logs 0 · verify yes
cycle-0056 selected recurring_friction inline-interpreter-source (3 bites, 3 lanes, trailing 7d -- all three on 2026-08-31). Mechanism census of the 3: bites 1-2 (clerk-signup-reconcile-nightly 08:45Z, prod-drift-sentinel 10:55Z) were SYNC cases where --pysrc already existed and was not reached first; bite 3 (score-import-shepherd 13:32:57Z, DETACHING a cohort audit) had no safe entry at all -- --

### FU-035 [P2/open] The $25 budget guard's MTD half is fail-open, and its threshold is a lagging indicator
filed 2026-07-20 (44d) · last touch 2026-09-01 (1d) · source vast-jobs-daily-audit · logs 15 · verify yes
Two defects in the spend half of the daily ops audit, found because today's run had to create the state file from scratch. **(1) FAIL-OPEN METRIC:** MTD spend is specified as a delta against `{date, balance}` history persisted in `D:\zo\runs\ops_audit_state.json` — that file **did not exist** this morning (the whole `D:\zo\runs\` path had no such file), so the MTD figure has never actually been co
cites: FU-192, FU-207

### FU-363 [Punspecified/open] 2026-09-01 | mcplookup-nightly-db-backup | THE REPAIR THE ERROR MESSAGE INVITED WAS THE REGRESSION, AND `REVERT_FAILED` HAS NO DOOR OUT
filed 2026-09-01 (1d) · last touch 2026-09-01 (1d) · source mcplookup-nightly-db-backup run 20260901T071010Z (lane_start rc=1, peer_review sweep) · logs 1 · verify yes
cites: FU-313, FU-330, FU-344

### FU-365 [Punspecified/open] A paid GPU instance whose launcher printed `LEFT ALIVE` bills forever, and the audit's wedge guard is keyed on a state it can never be in
filed 2026-09-01 (1d) · last touch 2026-09-01 (1d) · source unspecified · logs 0 · verify yes

### FU-367 [Punspecified/open] The reachability ratchet's baseline went stale, converting a derivative gate into a level gate that fails 25 of 45 open PRs on inherited debt
filed 2026-09-01 (1d) · last touch 2026-09-01 (1d) · source unspecified · logs 1 · verify yes
`capmap-check` fails 25 of the newest 45 open PRs with one identical verdict: `REGRESSION (orphans=335 baseline=277 delta=+58 mode=enforce)`, emitted by `tools/reachability_ratchet.py --enforce`. The ratchet's stated contract is to gate the DERIVATIVE -- to stop SILENT ORPHAN GROWTH introduced by the PR under test -- and `pr-gates.yml` says so in its own comment: "the baseline is pinned at 276 (th

### FU-372 [P2/open] the phantom `app.dependency_overrides` family is 36 sites, not 6 and not 14 -- both censuses scoped on the NAME they arrived with, and the thing that does not exist is the MODULE
filed 2026-09-01 (1d) · last touch 2026-09-01 (1d) · source follow-up-triage--implement-agent-for-the-zo-sentinel-project · logs 0 · verify yes

### FU-374 [P2/open] loop_health's stall trend splits on OBSERVED days, so a 6-day scheduler dormancy reads as a RISING stall rate
filed 2026-09-01 (1d) · last touch 2026-09-01 (1d) · source improvement-loop · logs 0 · verify yes

### FU-110 [P2/open] graphify-KL daily refresh now reconciles open-FU code-anchors against the fresh graph (drift monitor + subgraph cache)
filed 2026-07-26 (38d) · last touch 2026-09-02 (0d) · source graphify-kl-daily-refresh · logs 30 · verify yes
The daily Graphify KL task was extended (chairman-requested) with a second phase that keeps the FOLLOWUPS ledger consistent with the freshly-built code graph — the ledger↔KL half of [[FU-103]]. Every open FU names `.py` code anchors; the KL is the loop's self-description of that code; so an anchor whose basename is absent from `graph.json` means the ledger's picture has drifted (module unbuilt, re
cites: FU-061, FU-103, FU-107

### FU-168 [P2/open] seven "independent" verdicts are ONE confirmation counted seven times
filed 2026-07-29 (35d) · last touch 2026-09-02 (0d) · source daily-chairman-review (CofC seat 3) · logs 4 · verify yes
`prod_deploy_state.json` cited "the SEVENTH independent artifact on the same tree object" as evidence of strength. Seat 3 diffed all seven `verdict_7fc39201_*.json`: **identical except `checked_utc` and two gate-duration strings**. Seven deterministic re-runs on an unchanged input are one confirmation restamped, and the file contradicts itself — the 2026-07-28T16:49Z run had already ruled that re-

### FU-195 [P2/open] An EMPTY directory appeared in services/active during a build, and it is the exact shape that would fake the T2 milestone
filed 2026-07-30 (34d) · last touch 2026-09-02 (0d) · source daily-chairman-review · logs 29 · verify yes
`services/active/server_search/` was created 2026-07-30T12:25:47Z containing **zero entries** (`ls -A` = 0), while `promote_staged_to_active.py` had reported promote=0 / hold=178 three minutes earlier and `promoted: []`. Nothing legitimately promoted it. `server_search` is one of the 6 services revived in this run ([[FU-120]]) and was mid-build at the time -- its self-test went RED and it was ghos
cites: FU-028, FU-114, FU-120, FU-194, FU-207, FU-208, FU-231, FU-236, FU-249, FU-290, FU-313, FU-329
