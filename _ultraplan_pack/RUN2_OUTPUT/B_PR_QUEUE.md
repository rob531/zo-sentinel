# RUN 2 / Deliverable B — Open PR queue, classified by mechanism

Repo `rob531/zo-sentinel`. Measured 2026-09-02 (evening, local), read-only.
Clone: `D:\zo\_ultraplan_run\zo-sentinel`, branch `ultraplan/pack-20260902` @ `0e92b5b3`.
`origin/main` at measurement time: **`b591062f`** (main had already moved past the clone's HEAD).

All PR head commits were fetched locally so every git measurement below is a full census,
not a sample:

```
git fetch origin '+refs/pull/*/head:refs/remotes/pr/*' --prune
  FETCH_RC=0   PRREFS=4458   MAIN=b591062f63d853b93593a5a4f43a8ff6b0a489ef
```

---

## 0. The count, and the cap check

```
gh pr list --state open --limit 1000 --json number,title,createdAt,updatedAt,author,files,headRefName,isDraft,labels,baseRefName
  -> 285 rows
```

**285 open PRs**, not 282. The limit was 1000 and 285 came back, so **285 is a count, not a
cap**. The brief said 282; the queue moved between that probe and this one. Oldest
`createdAt` 2026-07-19T12:56:39Z, newest 2026-09-02T22:47:59Z. Authors: `rob531` 281,
`app/dependabot` 4. Zero drafts. All 285 target `main`.

Age (by `createdAt`): 107 under 7d · 97 at 7–20d · **81 at 21d+**.
Age (by `updatedAt`): 210 under 7d · 39 at 7–20d · 36 at 21d+.

---

## 1. The `mergeable` field is unusable tonight — confirmed, and it is worse than described

The brief reported `253/22/1` then `60 MERGEABLE / 222 UNKNOWN` about two hours apart with
nothing changed about the PRs. That is GitHub recomputing its lazily-cached mergeability after
`origin/main` moved. **Nothing in this document is classified on `mergeable`.**

Two consequences worth carrying forward:

1. `tools/pr_triage.py` — the thing that produces the `triage:*` labels — reads that field.
   It is partly insulated (`solid` accepts `MERGEABLE`, `UNKNOWN` *and* empty; only
   `CONFLICTING` forces `stale`), so the labels are **not** pure noise. But `triage:stale`
   is `CONFLICTING **or** a required gate FAILED`, so a stale-cache `CONFLICTING` can still
   mint a `stale` label that outlives its cause. Treat the label set as a *hint*.
2. Label counts, for reference only (`triage:solid` 95 · `triage:scaffold` 82 ·
   `triage:stale` 81 · `triage:dup` 16 · unlabelled 11). Everything below is measured
   independently of these.

---

## 2. Structural groups (exact — all 285 classified by changed-file shape)

256 of 285 PRs change exactly **one** file; the other 29 change two.

| Group | n | Shape |
|---|---|---|
| **G1 — code into `services/active/<name>/`** | **65** | `router.py` ×55, `__init__.py` ×5, `dashboard.html` ×4, `view.py` ×1. **0 of 65 include a `service.toml`.** |
| **G2 — files into `services/staged/<name>/`** | **140** | `service.toml` ×89, `__init__.py` ×21, `router.py` ×12, `logic.py` ×9, `contract.py` ×8, `_impl.py` ×1 |
| **G3 — a loose file at the repo root** | **67** | e.g. `build_axis_probability_summary_router.py`. 26 of these also touch `tools/reachability_deferred.json` |
| **G4 — everything else** | **13** | 4 dependabot; 9 hand/wiring PRs (`app/main.py`, `zo_sentinel/*`, `ops/host/*`, `.github/*`, `tests/*`) |

G4 in full: 1767, 1795, 2178, 2329, 2330, 2779, 2836†, 3277†, 3693, 3765, 4024†, 4094, 4399†
(† = dependabot).

---

## 3. Mechanisms (exact counts — census of all 285 head SHAs against `/commits/{sha}/check-runs`)

Branch protection on `main` requires **8 contexts**:

```
gh api repos/rob531/zo-sentinel/branches/main/protection --jq .required_status_checks
  contexts: capmap-check, static-analysis, smoke-ladder, frontend, pytest,
            no-hollow, schema-prm, referent-verify        (strict: false)
```

I queried check-runs for all 285 head SHAs (`census.json`, 0 errors) and reduced to those 8.

| | count |
|---|---|
| PRs missing **≥1 required context entirely** (never reported) | **180 / 285** |
| …of those, PRs with **no failing check at all** — green everywhere they ran | **94** |
| PRs with ≥1 **failing** required context | **172 / 285** |
| PRs **fully green on all 8** | **19 / 285** |

Per-context:

```
MISSING   capmap-check 2 · static-analysis 2 · smoke-ladder 2 · frontend 2 · pytest 3
          no-hollow 85 · schema-prm 86 · referent-verify 179
FAILING   capmap-check 148 · pytest 73 · static-analysis 14 · no-hollow 11
          smoke-ladder 7 · schema-prm 7 · referent-verify 2 · frontend 2
```

| group | n | ≥1 missing | ≥1 failing | fully green |
|---|---|---|---|---|
| G1 active | 65 | 25 | **65 (100%)** | 0 |
| G2 staged | 140 | 90 | 75 | 15 |
| G3 root | 67 | 56 | 26 | 3 |
| G4 other | 13 | 9 | 6 | 1 |

### M1 — Born unmergeable: 65 PRs (was 16; it has quadrupled)

Every one of the 65 G1 PRs lands code under `services/active/<name>/` with **no
`service.toml`**, and **all 65 fail a required context**. The gate is right, twice over:

- `capmap-check` → failing step **"Spine strict validation (BLOCKING — every active service
  valid)"** (`tools/generate_spine.py --strict`), sampled on #3348, #4485, #4487, #2540.
- `pytest` → `FAILED tests/test_dockerfile_copy_covers_active_services.py::test_every_active_service_declares_a_resolvable_import_path`
  (also `::test_dockerfile_copies_every_active_service_root_module`).

The remedy is **relocate to staged, never close** (FU-309). This is not cosmetic: on
`origin/main` there are **1,136 staged service dirs and 32 active ones**, and **41 of these 65
PRs are the code half of a service whose manifest is ALREADY in `services/staged/` on main**
— the builder writes the manifest into staged (passes) and the router into active (fails), so
neither half ever completes. Of those 41, only 4 are true duplicates of a file already in
staged (#3344, #3363, #4167, #4246); **37 are genuinely new content pointed at the wrong lane.**

### M2 — A required context that never reports: 180 PRs, 94 of them otherwise green

`no-hollow`, `schema-prm` and `referent-verify` are required contexts fired **only by the
`pull_request` event**. A PR whose head has not been pushed since those contexts became
required simply never produces them, and branch protection waits forever. `referent-verify`
was added 2026-08-25 (`c6d8a14b`, #4036) and made required 2026-08-26 (#4089) — **179 of 285
open PRs have no `referent-verify` check-run at all.**

Worked example, #4186 (`triage:solid`, head `b04542c3`): 9 check runs, **every one success**,
and `no-hollow`/`schema-prm`/`referent-verify` absent. It presents as green and cannot merge.

**80 PRs currently have auto-merge armed and are still open** (`gh pr list --json
number,autoMergeRequest` → 80 non-null of 285). They are queued behind contexts that will
never arrive on their own.

The repo already knows this: PR #4392 (merged `925748e3`, 2026-09-01) — *"delete the PAT
dependency — make every required gate dispatchable"* — gave `no-hollow` and `schema-prm` a
`workflow_dispatch` so `pr-relander` can produce all 8 contexts without a human push. **The
cure exists and is not working.** `gh run list --workflow pr-relander.yml --limit 15` on
2026-09-02 returns: failure, failure, success, failure, cancelled, failure, failure, cancelled,
failure, cancelled, failure, failure, cancelled — **1 success in 15**. Fixing `pr-relander` is
the single highest-leverage action available against this queue.

### M3 — Failing required gates, by which gate and why

`capmap-check` fails on 148 PRs. It is one job with several blocking steps; the failing step
differs by group (sampled failing-step names via `gh api .../actions/jobs/{id} --jq .steps[]`):

- G1 → *Spine strict validation* (see M1) — structural, 65 PRs.
- G3 root-file PRs → *Reachability ratchet (ENFORCE)* (#3821) — a new unmounted router that
  is neither mounted nor declared in `tools/reachability_deferred.json`.
- G2 manifest PRs → *Service-manifest shape gate (BLOCKING — FU-120)* (#4317).

### M4 — Red but empty: **FALSIFIED for this queue.** Do not act on this hypothesis tonight.

I pulled the actual `pytest` job logs for three failing PRs (#4487, #3348, #2540):

```
#4487  1 failed, 660 passed in 163.24s
#3348  1 failed, 639 passed in 164.26s
#2540  1 failed, 639 passed in 164.84s
```

The red `pytest` is running 639–660 tests and failing exactly one — the
`test_dockerfile_copy_covers_active_services` assertion from M1. **The count moved; this is a
real verdict, not the 87-PR "red and empty" dam.** (COULD_NOT_DETERMINE for the other five
required contexts — I read step names and conclusions for those, not test counts. Settling it
would mean pulling `--log-failed` for a sample of each context.)

### M5 — Green on a dead tree: measured on all 285, and it is SMALL

For every PR: `git merge-base origin/main pr/N`, then
`git log <mb>..origin/main --oneline -- <that PR's files>`.

- Distance from main: **136 PRs are 200+ commits behind**, 79 are 51–200, 66 are 1–50, 4 are level.
- **Only 24 of 285** have had `origin/main` touch any file they change since their merge-base.
- **18 of 285** propose a "new file" at a path that **already exists on `origin/main`**.
- Restricted to the 81 PRs older than 21 days: 19 have moved files, 8 have collided paths.

So: the queue is *stale* (nearly everything is hundreds of commits behind) but only ~8% of it
is *grading a superseded tree*, because 256/285 PRs add a single brand-new file nobody else
touches. The dangerous ones are the hand PRs, where merging would **revert** main:

| PR | age | `git diff --stat origin/main pr/N` |
|---|---|---|
| **2178** fix: healing the ops-audit state file | 35d | 96 insertions, **414 deletions** vs main |
| **4094** referent-verify must run and stay required | 6d | 26 insertions, **52 deletions** vs main — and main **already contains** the fix (`git show origin/main:.github/workflows/referent-verify.yml` has both `REQUIRED CHECK since 2026-08-26` and `NO paths: filter`). **#4094 is superseded; merging it would partially undo what landed.** |
| 2779 fix(runbook) deploy_prod.ps1 | 28d | 28 insertions, 9 deletions vs main |
| 1767 / 3765 wire-into-`app/main.py` | 40d / 10d | both target a file main has moved 3 times since |

### M6 — Superseded / duplicate: 19 (structural), 16 (label)

Independent of the labels: 8 PRs share a primary changed path with a **higher-numbered open**
PR, and 11 share an identical `build: <task>` title with a higher-numbered open PR — union
**19**. Across the 205 service PRs there are **180 distinct service names**, so genuine
duplication is a minor part of this queue, not its explanation.

### M7 — Held on purpose, not broken: 13 + 2

- **13 PRs** touch paths matching the convergence-freeze pattern in `auto-merge.yml`
  (`trust_synthesiser|risk_ranker|enrichment|signal_analyser|mcp_llm_`): 3784, 4418, 4422,
  4423, 4424, 4429, 4430, 4437, 4438, 4439, 4446, 4448, 4484. **12 of these are among the 19
  fully-green PRs** — i.e. most of the "why isn't this merged, it's green" set is a
  deliberate training-convergence hold, not a defect.
- **2 PRs** touch `.github/` (2836, **4094**) and are blocked by the CI-surface guard added
  2026-09-01. Human merge only, by design.

### M8 — Abandoned: **0**

All 285 are authored by `rob531` (281) or `app/dependabot` (4). There is no inactive-author
class here. 36 PRs have not been updated in 21+ days, but the author is live and the machine
that opened them is still opening more (newest PR is 5 hours old).

---

## 4. Does Run 1's "scaffold flood, not a merge queue" hold?

**Yes, and the sharper statement is: it is a flood of *halves*.** 256/285 PRs are single-file.
The builder emits a service in pieces across separate PRs and puts the pieces in different
lanes — manifest to `staged` (passes the gates), code to `active` (fails two of them). 41
current PRs are the orphaned code half of a manifest already sitting on main.

But the *content* is not empty. I read the diffs: #3348 is a real 138-line coverage API, #3616
a 286-line precision audit, #3356 a 297-line staleness probe, #4487 a 375-line probe. Calling
these "scaffolds" understates them — they are working routers filed into a lane that refuses
them. The G3 root-file PRs are a different animal: host daemon scripts hardcoding
`/home/workspace/...` and `http://localhost:8772` dropped at the repo root (#4192, #4191 —
#4191 calls `requests.post` without importing `requests`). Those are not repo code at all.

---

## 5. Is this queue a blocker to R1/R2/R4? — **No. It is independent debt.**

The binding constraint is downstream of the queue, and I can show it:

```
git ls-tree -r --name-only origin/main services/staged/ | ...
  staged service dirs on main:          1,136
  ...with a service.toml:               1,057
  ...COMPLETE (service.toml + a .py):     508
git ls-tree --name-only origin/main services/active/
  active service dirs on main:             32
```

**508 staged services on main are already complete and unpromoted.** Merging the entire PR
queue tonight would move that number, not the corpus floor, not `oldest_scored_at`, and not
the dashboard. The staged→active promoter — and the 1,229→464→32 attrition already on record —
is the R1/R2 constraint. The PR queue is debt that costs review attention, not throughput.

### PRs that touch R1 / R2 / R4 subject matter (named, with the honest caveat)

None of these can merge as filed; **all four are M1 (code in `services/active`, no manifest)**
and every one fails `capmap-check` + `pytest`. Each is one path rewrite (`services/active/…` →
`services/staged/…`) from passing — and even then lands in the unpromoted pool of 508.

| PR | R | What it actually contains | State on main |
|---|---|---|---|
| **#3348** | **R1 + R4** | `services/active/scoring_coverage_api/router.py`, 138 lines. `GET /api/scoring/coverage` returning `total_servers / llm_scored_servers / legacy_scored_servers / coverage_pct`. This is precisely the "stop calling unassessed rows scored" split. | `services/staged/scoring_coverage_api/` exists with **only `__init__.py`** — no manifest, no router. Both halves still missing. |
| **#3616** | **R1** | `services/active/score_precision_audit_report/router.py`, 286 lines — per-axis entropy, `mean_p_top`, `cv_p_top`, distinct label counts, model-version counts. A defensibility instrument. | `services/staged/score_precision_audit_report/service.toml` is **already on main**. This PR is its missing code half. |
| **#3356** | **R2** | `services/active/score_staleness_probe/router.py`, 297 lines — `GET /api/scoring/staleness-report`, buckets `<1h … >30d` plus unscored, with example server ids. Reports staleness; it does **not** select a rescore cohort. | `services/staged/score_staleness_probe/service.toml` **already on main**. Missing code half. |
| **#3375** | **R1** | `services/staged/orphan_router_census/service.toml` — a manifest whose `import_path` is `services.active.orphan_router_census.router`, a module that does not exist in either lane. | Manifest with no referent. Merging it adds a dangling entry. |

Adjacent but **not** R1/R2/R4 despite the name match: #4487 / #4497 / #4498
(`write_service_staleness_probe`) probe the write_service **daemon's** heartbeat, not score
freshness. #4192 / #4191 (`scoring_frequency_*`) are host daemon scripts at the repo root, not
repo services.

**No open PR advances R2 in the sense the roadmap needs** — nothing in the queue selects a
rescore cohort, and nothing can move `oldest_scored_at`, which reads a `max()` over a corpus
floor frozen since 07-19 (FU-361). That work does not exist in this queue.

---

## 6. COULD_NOT_DETERMINE

- **Mergeability / conflict state of any PR.** GitHub's cache is mid-recomputation. *Settled
  by:* re-probing `mergeable` after main has been quiet for ~1h and requiring two identical
  consecutive readings before believing either.
- **Whether the 5 required contexts other than `pytest` are red-and-empty.** I have step names
  and conclusions, not collected-test counts, for `smoke-ladder`, `static-analysis`,
  `no-hollow`, `schema-prm`, `frontend`. *Settled by:* `gh run view <runid> --log-failed` on
  one failing sample per context and grepping for a collected/ran count.
- **Why 1684, 3082 and 3088 sit open while fully green on all 8 required contexts** (they are
  not in the freeze set and not dependabot). Most likely a real conflict, which is exactly what
  I refuse to assert tonight. *Settled by:* a local `git merge-tree origin/main pr/N` — a
  measurement that does not use GitHub's cache at all.
- **Whether the `triage:*` labels are current.** #3821 carries `triage:solid` while
  `capmap-check` is failing on its head SHA, so at least one label is stale relative to its
  own subject. I did not date the labels. *Settled by:* `gh api .../issues/{n}/timeline` for
  `labeled` events vs the check-run `completed_at`.
- **Whether `pr-relander`'s failures are one cause or many.** I read the 15-run
  success/failure list, not the logs.

---

## 7. Artifacts (all under `D:\zo\_ultraplan_run\`)

`pr_list.json` (285 PRs, UTF-16 — PowerShell redirection BOM) · `deadtree.json` (285 rows:
merge-base, behind-count, main-touched-file count, path-collision flag) · `census.json` (285
rows: missing/failing required contexts per head SHA) · `checks.json` (56-PR deep sample, full
check-run detail) · `an1..an19.py` + `.out` (the reductions above).

**On sampling:** the group counts in §2, the check-run counts in §3, and the dead-tree counts
in §5 are **full censuses of all 285 PRs**, not extrapolations. Only three things are sampled
and stated as such: the failing *step name* inside `capmap-check` (6 PRs), the `pytest`
collected-test counts (3 PRs), and the diff-content reads (11 PRs).
