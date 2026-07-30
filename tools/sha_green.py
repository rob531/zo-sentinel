#!/usr/bin/env python3
"""sha_green.py -- resolve whether a commit sha is GREEN on the REQUIRED contexts.

    python tools/sha_green.py --sha <SHA> [--json] [--repo rob531/zo-sentinel]

    rc 0 = GREEN    -- all 7 required contexts completed/success
    rc 1 = RED      -- at least one required context concluded `failure`
    rc 2 = UNKNOWN  -- cannot evaluate (no checks, in-flight, or partially absent)

NEVER read rc=2 as GREEN and never read it as RED. Unknown is not zero (R6).

WHY THIS EXISTS
---------------
Every prod-drift-sentinel slot answered "is main green?" in ad-hoc shell, and on
2026-07-30T19:2xZ that ad-hoc query was pointed at the WRONG ENDPOINT. These two
calls, against the SAME sha de068560, disagree completely:

    gh api repos/O/R/commits/<sha>/status      -> all 7 required contexts ABSENT
    gh api repos/O/R/commits/<sha>/check-runs  -> all 7 required contexts SUCCESS

The 7 required contexts are GitHub Actions CHECK RUNS, not legacy commit
STATUSES, so the /status surface reports an empty `statuses` array and every
context reads ABSENT. Nothing errors. A slot that drifted to /status would have
concluded "main not shippable" and stalled the stage forever, on a gate whose
only honest verb was EXCLUDED -- a gate that can never go green. That is the 51%
class again: the artifact you inspected is not the artifact that answers.

THREE THINGS THIS TOOL DOES THAT THE AD-HOC LOOP DID NOT
--------------------------------------------------------
1. LOUD ON EMPTY. If the evidence source returns zero check-runs, that is rc=2
   UNKNOWN -- not "no failures, therefore green", and not "all absent,
   therefore red". An empty answer is the shape both a healthy skip and a
   wrong endpoint produce, so it may never resolve to a verdict.

2. LATEST RUN PER NAME, and it says when there was more than one. A sha can
   carry several check-runs with the SAME name (re-runs): faaf7c00 carries
   three `pytest` runs. The ad-hoc loop took `$m[0]` -- whichever the API
   happened to list first -- so a re-run that flipped the answer was decided by
   ordering. We sort by started_at and take the newest, GitHub's own rule, and
   print `(N runs)` so a reader can see a re-run happened.

3. TIP-SHA REDIRECT. 6 of the 7 required contexts are `on: pull_request`, so on
   a main-TIP sha they are ABSENT BY CONSTRUCTION rather than failing (the
   standing R3 caveat). Rather than making each slot hand-walk to the merged
   PR's head, this resolves the associated PR and re-answers there, naming the
   redirect in `basis`. The verdict always states WHICH sha it was computed on.

`skipped`, `cancelled` and `neutral` are NOT passes (R3). `treewalk-smoke` is a
removed check and is ignored (FU-084). `triage` is CANCELLED BY DESIGN and is
NOT a required context, so its state is evidence in neither direction (ruled
2026-07-30) -- it is simply absent from REQUIRED.

NEGATIVE CONTROL (R4) -- this tool has been SEEN RED, not merely seen green:
    --self-test runs all three verdicts against real shas in this repo.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REPO = "rob531/zo-sentinel"

# The 7 contexts that actually gate a merge. Hand-maintained, and that is a
# known limit: a context added to branch protection but not added here is
# UNMEASURED, and unmeasured is not passing.
REQUIRED = (
    "capmap-check",
    "static-analysis",
    "smoke-ladder",
    "frontend",
    "pytest",
    "no-hollow",
    "schema-prm",
)

# Removed check (FU-084) -- present in old branch-protection lore, never rerun.
IGNORED = ("treewalk-smoke",)

GREEN, RED, UNKNOWN = 0, 1, 2


def _gh(path: str):
    """One gh api call. Returns parsed JSON, or None if the call itself failed."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def fetch_check_runs(repo: str, sha: str):
    """All check-runs for a sha, paginated. None means the query itself failed --
    which is distinct from a sha that genuinely has zero check-runs."""
    runs, page = [], 1
    while True:
        j = _gh(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        if j is None:
            return None
        batch = j.get("check_runs") or []
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1
        if page > 20:                      # hard stop; 2000 runs is not a real sha
            return runs


def latest_per_name(runs):
    """Collapse re-runs: newest started_at wins, and remember how many there were."""
    by_name = {}
    for r in runs:
        n = r.get("name")
        by_name.setdefault(n, []).append(r)
    out = {}
    for n, rs in by_name.items():
        rs.sort(key=lambda r: (r.get("started_at") or "", r.get("id") or 0))
        out[n] = (rs[-1], len(rs))
    return out


def associated_pr_head(repo: str, sha: str):
    """The PR this sha came from -- used only to redirect a tip sha to the head
    where the required contexts actually ran."""
    j = _gh(f"repos/{repo}/commits/{sha}/pulls?per_page=10")
    if not j:
        return None, None
    for pr in j:
        if (pr.get("merge_commit_sha") or "") == sha:
            return pr.get("number"), (pr.get("head") or {}).get("sha")
    pr = j[0]
    return pr.get("number"), (pr.get("head") or {}).get("sha")


def _judge(repo: str, sha: str, allow_redirect: bool):
    runs = fetch_check_runs(repo, sha)
    if runs is None:
        return dict(verdict="UNKNOWN", rc=UNKNOWN, sha=sha, reason="query_failed",
                    detail="gh api check-runs did not return usable JSON -- "
                           "no verdict is available, which is not the same as a red one.",
                    contexts={}, total_runs=0)

    latest = latest_per_name(runs)
    present = {n: latest[n] for n in REQUIRED if n in latest}

    if not runs:
        return dict(verdict="UNKNOWN", rc=UNKNOWN, sha=sha, reason="no_check_runs",
                    detail="This sha carries ZERO check-runs. That is the same shape a "
                           "wrong endpoint produces, so it resolves to UNKNOWN -- an "
                           "absence of failures is not evidence of success (R4/R6).",
                    contexts={}, total_runs=0)

    # Redirect when the required set is INCOMPLETE -- not only when it is wholly
    # absent. `pytest` also runs `on: push`, so a main-tip sha carries exactly one
    # of the seven; an "all absent" test would never fire here. (Found by running
    # this against the live tip after the self-test fixtures already passed --
    # pre-flight cases must mirror real runtime variance, not just the clean shapes.)
    missing_required = [n for n in REQUIRED if n not in present]
    if missing_required and allow_redirect:
        num, head = associated_pr_head(repo, sha)
        if head and head != sha:
            sub = _judge(repo, head, allow_redirect=False)
            # Only ADOPT the redirect when it is conclusive. If the PR head is
            # itself incomplete, redirecting has not bought an answer, and the
            # honest verdict is the one about the sha actually asked about.
            if sub["verdict"] in ("GREEN", "RED"):
                sub["basis"] = (
                    f"REDIRECTED: {len(missing_required)} of {len(REQUIRED)} required contexts are "
                    f"`on: pull_request` and are ABSENT BY CONSTRUCTION on tip sha {sha[:8]}; "
                    f"resolved instead on PR #{num} head {head[:8]}, which is where they ran.")
                sub["tip_sha"] = sha
                sub["pr"] = num
                return sub

    contexts, failures, unfinished, absent = {}, [], [], []
    for name in REQUIRED:
        if name not in present:
            absent.append(name)
            contexts[name] = "ABSENT"
            continue
        run, n_runs = present[name]
        status, concl = run.get("status"), run.get("conclusion")
        label = f"{status}/{concl}" + (f" ({n_runs} runs)" if n_runs > 1 else "")
        contexts[name] = label
        if status != "completed":
            unfinished.append(name)
        elif concl == "failure":
            failures.append(name)
        elif concl != "success":
            # skipped / cancelled / neutral / timed_out -- a SKIP IS NOT A PASS (R3)
            unfinished.append(name)

    if failures:
        return dict(verdict="RED", rc=RED, sha=sha, reason="required_failure",
                    detail="failing required contexts: " + ", ".join(sorted(failures)),
                    contexts=contexts, total_runs=len(runs))

    if absent or unfinished:
        bits = []
        if absent:
            bits.append("absent: " + ", ".join(sorted(absent)))
        if unfinished:
            bits.append("not completed/success (in-flight, skipped or cancelled -- "
                        "a skip is not a pass): " + ", ".join(sorted(unfinished)))
        return dict(verdict="UNKNOWN", rc=UNKNOWN, sha=sha, reason="incomplete",
                    detail="; ".join(bits) + ". In-flight is NOT red (FU-195) and absent "
                           "is NOT green -- this is cannot-evaluate.",
                    contexts=contexts, total_runs=len(runs))

    return dict(verdict="GREEN", rc=GREEN, sha=sha, reason="all_required_success",
                detail=f"all {len(REQUIRED)} required contexts completed/success",
                contexts=contexts, total_runs=len(runs))


def judge(repo: str, sha: str):
    res = _judge(repo, sha, allow_redirect=True)
    res.setdefault("basis", f"resolved directly on {sha[:8]} via "
                            f"repos/{repo}/commits/<sha>/check-runs (NOT /status)")
    res["ignored_contexts"] = list(IGNORED)
    res["required"] = list(REQUIRED)
    return res


def render(res: dict) -> str:
    lines = [
        f"repo     : {REPO}",
        f"sha      : {res['sha']}",
        f"basis    : {res['basis']}",
        f"runs     : {res['total_runs']} check-runs on the answering sha",
    ]
    for name in REQUIRED:
        lines.append(f"  {name:<16} {res['contexts'].get(name, 'ABSENT')}")
    lines.append(f"VERDICT  : {res['verdict']} rc={res['rc']} -- {res['detail']}")
    return "\n".join(lines)


def self_test(repo: str) -> int:
    """R4: prove each verdict on a REAL sha in this repo, including a red one.
    An assertion never seen RED is unproven, not passing."""
    cases = [
        ("GREEN", "de06856006e06890d1634d182ff6f9a9b93af13e",
         "PR #2418 head -- all 7 required contexts success"),
        ("RED", "faaf7c00660f5d2c11dca4448773ccd51865a138",
         "static-analysis concluded failure (and carries 3 `pytest` re-runs, "
         "so it also exercises latest-per-name)"),
    ]
    root = subprocess.run(["git", "rev-list", "--max-parents=0", "origin/main"],
                          capture_output=True, text=True)
    if root.returncode == 0 and root.stdout.strip():
        cases.append(("UNKNOWN", root.stdout.strip().splitlines()[-1],
                      "repo root commit -- predates CI, zero check-runs"))

    # A main-TIP sha: 6 of 7 required contexts absent by construction, `pytest`
    # present because it also runs on push. This is the shape that actually
    # occurs every slot, and the shape the first cut of the redirect MISSED --
    # its guard was `not present`, which a tip sha never satisfies.
    tip = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True)
    if tip.returncode == 0 and tip.stdout.strip():
        cases.append(("GREEN", tip.stdout.strip(),
                      "origin/main tip -- must resolve via PR-head redirect, "
                      "NOT by reading 6 absent contexts as a verdict"))

    bad = 0
    for expected, sha, why in cases:
        got = judge(repo, sha)
        ok = got["verdict"] == expected
        bad += 0 if ok else 1
        print(f"[{'ok  ' if ok else 'FAIL'}] expect {expected:<7} got {got['verdict']:<7} "
              f"{sha[:8]}  ({why})")
        if not ok:
            print("        " + got["detail"])
    print(f"\nself-test: {len(cases) - bad}/{len(cases)} verdicts as expected"
          + ("" if bad else " -- GREEN, RED and UNKNOWN each demonstrated on real data"))
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sha")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test(a.repo)
    if not a.sha:
        ap.error("--sha is required (or use --self-test)")

    res = judge(a.repo, a.sha)
    print(json.dumps(res, indent=2) if a.json else render(res))
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
