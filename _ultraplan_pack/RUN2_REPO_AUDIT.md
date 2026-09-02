# ULTRA PLAN — RUN 2: REPO AUDIT AGAINST THE SURVIVING PLAN

Run this **after** Run 1. It consumes Run 1's output.

Attach: the `pack/` directory, Run 1's decision table and six-week plan, and the
repo at `D:\zo\zo-sentinel\zo-sentinel` (remote `rob531/zo-sentinel`).

---

## Who you are in this run

Run 1 decided what the system should be doing. This run asks whether the code can
carry it — and what in the codebase is now dead weight against that plan.

The ledger tells you what people noticed. The repo tells you what is actually
there. These have diverged before, in both directions: a docstring asserted a
cure the code did not contain, and a 20KB tool sat at zero callers because the
question it answered lived only in a docstring.

## Before anything else: which tree are you reading?

Two traps, both previously fired:

1. **The worktree is not `main`.** At pack build time the local checkout sat on
   `goose/fu119-shim-canary-tier` from 2026-08-10, while `origin/main` was
   somewhere else entirely. `30_prod_state.json` records both. **Audit against a
   fresh clone of `origin/main`** — the worktree once claimed 14 promotable
   items when the image could carry 4.
2. **`_tools\` is not a git repo.** It lives beside the repo on the tower and is
   not versioned with it. Anything you find there is unreviewable by any peer and
   uncarryable by any image. That is a finding, not a footnote.

## What to audit

### 1. Reachability — the question that has bitten hardest

For every cure the ledger claims landed, ask **not** "is it correct?" but:

- Is it reachable from the surface that was bitten? A remedy that exists but
  cannot be invoked from where the failure happens is not a cure. The record here
  is 15 of 16.
- Is it wired into *every* door of its shape, or one of eight? Census every call
  site of the same shape, in the same commit.
- Is it *adopted*, or merely present? Existence is not adoption. A component is
  landed when a census can tell used from unused — not when it works.

`_tools/` holds ~659 files. Some fraction are dark: never called by anything. The
ledger records that seven dark tools were one dead chain, and that an unwired
cure rots — one sat dark for 27 days and was wrong twice when finally run.

**Produce a census of `_tools/`:** for each file, is it invoked by a lane prompt,
by another tool, by CI, or by nothing? Group the "by nothing" set into (a) one-shot
scripts that correctly finished their job, (b) probes kept as evidence, (c) cures
that were built and never wired. Only (c) is a finding — and for each one, **run
it and check its arithmetic before recommending it be wired.** A dark tool is an
untested tool.

### 2. Test seams

The standing rule is: run your verify before the change and require it RED. No
test seam means no control by construction.

- Which of Run 1's KEEP items have a test seam, and which cannot be verified
  without building one first?
- CI's pytest is a **per-file allowlist**. A green whose collected-test count did
  not move ran none of your tests. For any test you propose, predict the count
  change before it is pushed.
- The ledger records that the required `pytest` check has never collected a
  single moat-rescore test — the GPU-spending path. Verify whether that is still
  true and say so either way.

### 3. The PR queue

276 open PRs, oldest 2026-07-19: 253 mergeable, 22 conflicting, 1 unknown.
GitHub computes mergeability lazily — `UNKNOWN` is a third state, not a synonym
for mergeable, and the split moves between probes minutes apart. Re-read
`30_prod_state.json` rather than citing these figures.

This is a queue, but treat it as evidence about the factory rather than a chore.
Known failure modes in the record: 16 PRs born unmergeable by landing code in the
registry directory (the gate was right — relocate, never close); a red check that
ran zero tests dammed 87 PRs (merge on the COUNT, never the colour); a stale
baseline turned a derivative gate into a level gate and reddened 25 of 45.

**Before merging any aged PR**, check whether its tree still exists:
`git log <merge-base>..origin/main -- <files>`. A green on a dead tree is a green
about nothing.

Classify the queue: mergeable-now / needs-rebase / born-unmergeable /
superseded-by-later-work / abandoned. Give counts and the mechanism behind each
group — not a list of 275 numbers.

### 4. Dead weight against the surviving plan

Run 1 said what the system should stop doing. Find the code that exists only to
serve what it should stop doing. Name it, size it, and say what deleting it would
break.

## Output

1. **`_tools/` census** — used / unused / never-wired, with the (c) set triaged
   after actually running them.
2. **PR queue classification** — by mechanism, with counts.
3. **Reachability report** — for each cure Run 1 kept, is it reachable, wired at
   every door, and adopted? Cite the call sites.
4. **Test-seam gap list** — Run 1 items that cannot be verified today, and what
   seam each needs.
5. **Deletion list** — with blast radius per item.

Every claim cites a path and a line, or a command and its output. A claim with
neither is an opinion, and this system has a standing finding that prose is not
enforcement.

## What you may not do

- No direct file writes to the repo. Changes go through PRs.
- Do not bypass `write_service`. Do not change ports. Do not touch `go.sh`. Ask.
- Do not run anything that rents a paid GPU. Cost ceilings: $3/wave, $8/week,
  hard halt at $25 MTD.
- Do not propose a cure the hazard corpus records as dead — re-read
  `22_hazards.md` if you are about to propose a fix that feels obvious.
