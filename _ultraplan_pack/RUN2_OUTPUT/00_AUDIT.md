# RUN 2 — REPO AUDIT AGAINST THE SURVIVING PLAN

**Date:** 2026-09-02 · **Inputs:** Run 1 output (`RUN1_OUTPUT/`), pack built
2026-09-02T21:50Z, repo at `origin/main` = `a2353dcc` via a clean clone.
**Method:** three parallel read-only audits — `A_TOOLS_CENSUS.md`,
`B_PR_QUEUE.md`, `C_REACHABILITY_SEAMS.md`. This file is the synthesis; the
detail and the citations live in those three.

---

## The finding that neither audit could see alone

**The PR queue is not stuck on conflicts. It is stuck on gates that never fire —
and the tool that would unstick it is one of the dark tools.**

Audit B: **180 of 285 open PRs wait on a required context that never reports.**
`referent-verify` is absent on 179 of them, `schema-prm` on 86, `no-hollow` on
85. Those checks fire only on `pull_request` events, so a PR whose branch was
never re-pushed never produces them. **94 of the 180 are green everywhere they
actually ran, and 80 have auto-merge armed** — queued forever behind a context
that will never arrive. The intended cure (#4392, dispatchable gates via
`pr-relander`) landed 2026-09-01 and is **broken: 1 success in its last 15 runs**.

Audit A, independently: `_tools/pr_regate.py` is a **cure built and never wired**,
and its `--dry-run` says it would **close and reopen 84 PRs**.

Closing and reopening a PR emits a `pull_request` event. That is precisely the
event the 180 stalled PRs never received. **`pr_regate.py` is a plausible direct
cure for the queue's dominant mechanism, and it has been sitting dark.**

Do not run it on 84. Two standing findings forbid it: a dark tool is by
definition untested, and audit A proved that concretely — **two of the eight
never-wired tools are wrong**, including `pr_block_census.py`, which silently
drops 45 of 239 blocked PRs and understates the red population by 34%. A capped
bulk command also picks by ORDER, not by merit.

**Validate on exactly one PR**, chosen from the 94 that are green-everywhere-they-
ran, and require the missing contexts to appear before touching a second. If it
works, that is the highest-leverage action available against this queue.

---

## Where Run 2 falsified Run 1

Run 1 read partially (91K tokens against a 600K pack) and hedged appropriately.
Three of its rows do not survive contact with the code:

| Run 1 said | Run 2 measured |
|---|---|
| FU-343 `SUPERSEDED?` — "classifier wired into all 9 tower-side doors" | **FALSIFIED.** `tower_path_doors.py --check` returns rc=1 today: 6 of 10 doors classify, **4 unguarded** — incl. `fu_verify.py`, which is named in **14 of 36 live lane prompts**. The census tool proving this has had zero readers since 2026-08-13. |
| FU-309 — 16 PRs born unmergeable | **65**, quadrupled. And it is wired at one door of two: `generate_spine.py --strict` covers `services/active` (32 dirs, 0 missing manifests) while `services/staged` has **1,133 dirs, 77 with no `service.toml`**; `check_service_manifests.py` globs manifests that exist, so it passes **vacuously**. |
| 282 open PRs | **285.** |

Audit B also tested Run 1's "red but empty" hypothesis against real logs
(`1 failed, 660 passed`, `639 passed` ×2) and **falsified it**. That dam is not
what is holding this queue. Good hypothesis, wrong here — do not act on it.

## Where Run 1 was right and the code confirms it

- **FU-359 is NO LONGER TRUE, and that is a genuine win.** The five
  `tests/test_rescore_*.py` files plus `test_vast_spend_selftest.py` entered the
  required allowlist via PR #4365 (`af6c32e5`, 2026-09-01) and collect **58
  tests**. The GPU-spending path is no longer untested.
- **The queue is independent debt, not an R1/R2 blocker** — confirmed. `main`
  carries **1,136 staged service dirs, 508 already complete, only 32 active.**
  Merging the entire queue moves the staged pool, not the corpus floor. **The
  promoter is the constraint**, which is the same shape as Run 1's "counting is
  solved, defensibility is not."
- **Tool adoption was never 26%.** The honest denominator is 130, not 472 —
  the rest are finished one-shots (235) and retained probes (107). Adoption is
  **122/130 = 93.8%**. Run 1's R6 instinct was right; the number it would have
  used was wrong.

## The roadmap cannot be verified yet — seams first

Audit C, per item:

| Item | Seam today | Consequence |
|---|---|---|
| **R1** instrument defensibility (P0) | **NONE.** `ukey`, `fabricated`, `unassessed` appear in **0 files** across `app/`, `tools/`, `schema/*.sql` | The P0 has no way to be measured or falsified. Build the seam before the feature. |
| **R2** unfreeze the floor (P0) | PARTIAL | `tools/rescore/spend_guard.py` — the module holding the **$3/$8/$25 ceilings** — has **zero tests anywhere**. **R2's cost failure condition is unfalsifiable today.** |
| **R3** retire pre-fix garbage | NONE (prod-side) | COULD_NOT_DETERMINE from the repo alone. |
| **R4** honest headline | PARTIAL | |
| **R5** shopfront | **NONE.** `sitemap` / `robots.txt` → 0 files | The indexability half does not exist to test. |
| **R6** fleet hygiene | **YES — today, no code needed** | Live sweep: 375 entries / 37 no-status, 36 lane dirs vs 21 registered. Do this one now. |

**CI reality:** the required `pytest` context is an explicit **48-file allowlist**
in `.github/workflows/evaluator.yml`. `tests/` holds **146 test files**, so
**98 (67%) are collected by no required check.** Collected baseline is **661**,
validated against CI rather than asserted.

**Free coverage available now:** four existing test files run green offline
(`69 passed in 6.60s`, rc=0) and are not allowlisted. Adding them takes the count
**661 → 730** (+10 `test_freshness_gate`, +42 `test_accept_gate`, +5
`test_tier_invariant`, +12 `test_fly_token`). Predict that number, then check the
green against the prediction — a green whose count did not move ran none of them.

## Deletion / archival

**606 of 736 `_tools` files** are archival candidates. **Archive, never delete:
`_tools` is not a git repo, so a deletion there is unrecoverable.** Fix
`dark_tools.py`'s root-only glob *before* relocating anything into
subdirectories, or the census goes blind the moment the files move.

Among the 8 never-wired cures: **2 are wrong** (`pr_block_census.py` drops 45 of
239; `pr_red_triage.py` is broken — 13/13 UNCLASSIFIED on 404s from expired job
logs), **1 has rotted** (`plan_200k_log_upsert.py` carries a 6-column header
against a 7-column live CSV and would drop `scores_rows`), and **2 work and are
reporting live reds nobody reads** (`tower_path_doors.py --check` RED now;
`claude_paths.py --scan` flags 6 files hardcoding the roaming view, including
`friction.py`).

## Warning about this branch

Audit A found that **merging the ultraplan pack would flip 3 dark tools green on
prose alone** — the pack's own text mentions them, and `dark_tools.py` counts a
prose mention as a caller. This branch is already marked NOT FOR MERGE; this is a
second, independent reason. It also means `dark_tools.py`'s caller test is
substring-based and will keep producing false greens until it distinguishes a
code reference from a mention.

---

## Recommended order

1. **R6 now** — it has a seam today and needs no code. Also fixes the no-status
   class that was this whole exercise's headline defect.
2. **Allowlist the four green-offline test files** — 661 → 730, free, and it
   gives R2/R4 something to fail against.
3. **Write R1's seam before R1** — the P0 currently cannot be measured at all.
4. **`spend_guard.py` tests** — until they exist, R2's cost ceiling is a claim,
   not a control, and the ceiling is what protects the $25 MTD hard halt.
5. **Validate `pr_regate.py` on ONE PR.** If the missing contexts appear, the
   queue's dominant mechanism has a cure.

## Held for the chairman

`pr_regate.py` on the full 84 PRs is **not** taken here and needs explicit
approval. So does any repair to `pr-relander` (#4392), which is the sanctioned
path and is currently failing 14 of 15 runs.
