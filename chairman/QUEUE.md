# Chairman queue — the work feed (layer L1)

Contract (`LOCO_CHAIRMAN.md` §9): ordered; top entry is the default next unit
of work; every entry is self-contained; an entry leaves only via a Gap
Register grade-move with evidence. Regenerable from the register — on
disagreement the register wins.

Seeded 2026-08-29 from the Gap Register. Grades are the register's
conservative seeds; each entry's step 1 is re-measurement.

---

## 1. GR-8 — linter --fix corrupts SQL names (unblocks 34 downstream decisions)
- grade C0 -> target C3 | class GC-12
- first command: `grep -rn "def .*fix" tools/model_import_linter* 2>/dev/null || find . -name "*model_import_linter*" -not -path './.git/*'`
- evidence for move: failing repro of the string-literal corruption; fix; sweep of every `--fix` call site; #4000 updated.

## 2. GR-6 — 7 FU predicates query nonexistent tables; UNKNOWN never escalates
- grade C0 -> target C4 | class GC-1
- first command: `sed -n '80,130p' tools/fu/fu_seed_predicates.py`
- evidence for move: names repointed per MERGE_AUDIT B1 suggestion; an age budget on UNKNOWN in `sql_assert`; a test that a catalog error cannot rest as UNKNOWN past budget.

## 3. GR-3 — columns check FAIL(114) but report-only; parse coverage UNKNOWN(4)
- grade C0 -> target C4 | classes GC-3, GC-1
- first command: `python tools/referent_verify.py --help 2>&1 | head -30`
- evidence for move: arming condition recorded ("arm at <= N" or date); the #4124 arming + #4128 disarm-hardening pair replicated for columns.

## 4. GR-1 — G6 truncation: patch parked; 583 unpaginated reads keep trusting `count`
- grade C2 -> target C4 | classes GC-5, GC-9
- first command: `sed -n '1,60p' docs/G6_BUS_TRUNCATION_CALLERS.md`
- evidence for move: operator decision re-asked per Decision Dock (apply+restart is operator-only); caller census taught to check `truncated`; #4003 pagination sweep started.

## 5. GR-4 — retry/reasoning-strip sweep: 10 censused files never swept
- grade C1 -> target C3 | class GC-4
- first command: `sed -n '55,75p' RETRY_GAP_SWEEP.md`
- evidence for move: each of the 10 scanned; patched or registered individually; zero remainder.

## 6. GR-2 — delegated gate on app.main (tier1/tier4 mutual deferral)
- grade C0 -> target C4 | class GC-2
- first command: `sed -n '390,410p' tests/ci/smoke_ladder.py`
- evidence for move: single declared owner; a test asserting every declared surface has exactly one owning gate; G1's defect-injection repro now caught.

## 7. GR-11 — graphify graph absent in fresh clones; structural claims unbudgeted
- grade C0 -> target C4 | classes GC-1, GC-8
- first command: `ls graphify-out/ && grep -rn graphify .github/workflows/ | head`
- evidence for move: graph rebuilt by CI on merge (F4); graph-derived claims carry build-time stamps.

## 8. GR-9 — builder-lane contention; archival record says retired, it never took effect
- grade C0 -> target C4 | class GC-8
- first command: `sed -n '53,110p' docs/FINDINGS_2026-08-23.md`
- evidence for move: one lane, or partitioned lanes, verified live on the host; record matches referent.

## 9. GR-10 — 6 phantom dependency_overrides imports; 25 staged services fail dry-run
- grade C0 -> target C3 | class GC-4
- first command: `grep -rn "dependency_overrides" --include='*.py' -l . | head`
- evidence for move: #4001 + #4002 site lists at zero remainder or individually registered.

## 10. GR-7 — verdict vocabulary drift (docs vs emitters; `unknown` ~82%)
- grade C1 -> target C4 | class GC-6 | S1 gate
- first command: `grep -n "verdict" DB_SCHEMA.md | head -20`
- evidence for move: canonical vocab declared in one file; emitters migrated or dated shims; a gate rejecting the dead dialect.

## 11. GR-5 — deferral list 63 > cap 40; escalation owed, not automatic
- grade C0 -> target C4 | class GC-7 | operator-coupled (#4004/#4005)
- first command: `ls tools/reachability_deferred.json 2>/dev/null; grep -n "over_review_cap" PRODUCT_SPEC.md | head -3`
- evidence for move: breach auto-fires the escalation; aging report on cadence; count under cap.

## 12. GR-12 — chairman state has no bus mirror
- grade C0 -> target C2 | class GC-4
- first command: `grep -n "mesh_memory" CLAUDE.md`
- evidence for move: write-through repo->bus per §11.2 implemented in the session-end protocol.

## 13. GR-13 — landing chain: verify the relander drains the backlog
- grade C2 -> target C3/C4 | class GC-13
- first command: `gh` unavailable in-session; check https://github.com/rob531/zo-sentinel/actions/workflows/pr-relander.yml runs + open-PR count vs the 117 baseline
- evidence for move: C3 = backlog measurably draining (open count + oldest-age down across two governance sessions); C4 = a test asserting each §13 chain link has an owner (workflow exists, label wiring intact — the #4128 disarm-hardening pattern for the landing chain).
