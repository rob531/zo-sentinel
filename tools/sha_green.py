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

FOUR THINGS THIS TOOL DOES THAT THE AD-HOC LOOP DID NOT
--------------------------------------------------------
0. THE REQUIRED SET IS READ FROM BRANCH PROTECTION, NOT FROM A LITERAL IN THIS
   FILE (FU-206). Branch protection is edited in the GitHub UI, outside this
   repo, so a hard-coded tuple can be silently wrong in the one direction that
   matters: a newly-required context that this tool never checks, on a sha it
   then calls GREEN. The literal survives only as a fallback for when the
   protection read FAILS, and the verdict always names which of the two it used.
   An EMPTY context list is refused rather than adopted -- zero required
   contexts would make every sha trivially green.

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

# The contexts that actually gate a merge are DERIVED FROM BRANCH PROTECTION at
# runtime (see resolve_required). This tuple is the FALLBACK used only when that
# read fails, and it is the LAST KNOWN GOOD value, not the authority.
#
# WHY THIS CHANGED (FU-206). The first cut of this tool hard-coded these seven
# and documented the weakness in a comment: "a context added to branch protection
# but not added here is UNMEASURED, and unmeasured is not passing." That comment
# was true and the code did nothing about it. A required context added by a human
# in the GitHub UI -- which is where branch protection is edited, not in this
# repo -- would never appear here, so this tool would report GREEN on a sha that
# could not merge. It is the 51% class in its purest form: the artifact this tool
# inspected (a literal in its own source) was not the artifact that decides.
REQUIRED_FALLBACK = (
    "capmap-check",
    "static-analysis",
    "smoke-ladder",
    "frontend",
    "pytest",
    "no-hollow",
    "schema-prm",
    "referent-verify",
)

# Resolved once per process by resolve_required(); every read goes through the
# module global so `_judge`, `render` and `judge` cannot disagree about the set.
REQUIRED = REQUIRED_FALLBACK
REQUIRED_SOURCE = {"source": "unresolved", "detail": "resolve_required() not yet called"}

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


def classify_protection(payload, fallback=REQUIRED_FALLBACK):
    """PURE. Given the branch-protection payload (or None), decide the required set.

    Separated from the network call so the negative controls below can drive it
    with the shapes that matter -- including the one that is dangerous.

    THE DANGEROUS SHAPE IS AN EMPTY `contexts` LIST. Read naively, "zero required
    contexts" makes every sha trivially GREEN: no context can be absent, none can
    fail, so the tool becomes a gate that can only go green (the mirror of the
    FU-186 gate that could only go red). An empty list is indistinguishable from
    a permissions-scoped token, a renamed branch or protection being briefly off,
    so it may NEVER be adopted as a verdict. It falls back and SAYS SO.
    """
    if payload is None:
        return tuple(fallback), {
            "source": "fallback_literal",
            "detail": "branch-protection read FAILED (no JSON) -- using the last known "
                      "good literal. The set may be STALE; a context added since is "
                      "UNMEASURED, and unmeasured is not passing (R6).",
            "trusted": False,
        }
    ctx = payload.get("contexts")
    if not isinstance(ctx, list) or not ctx:
        return tuple(fallback), {
            "source": "fallback_literal",
            "detail": "branch-protection returned NO contexts. An empty required set "
                      "would make every sha trivially GREEN, so it is refused outright "
                      "and the literal is used instead.",
            "trusted": False,
        }
    live = tuple(str(c) for c in ctx)
    added = [c for c in live if c not in fallback]
    dropped = [c for c in fallback if c not in live]
    info = {
        "source": "branch_protection",
        "detail": f"resolved {len(live)} required contexts from "
                  f"branches/main/protection/required_status_checks",
        "trusted": True,
        "drift_vs_literal": {"added": added, "dropped": dropped},
    }
    if added or dropped:
        info["detail"] += (
            f" -- DRIFT vs this file's literal: added={added or 'none'} "
            f"dropped={dropped or 'none'}. The LIVE set governs; the literal is "
            f"stale and should be updated in a follow-up PR.")
    return live, info


def resolve_required(repo: str, branch: str = "main"):
    """Set the module-level REQUIRED from live branch protection. Idempotent."""
    global REQUIRED, REQUIRED_SOURCE
    payload = _gh(f"repos/{repo}/branches/{branch}/protection/required_status_checks")
    REQUIRED, REQUIRED_SOURCE = classify_protection(payload)
    return REQUIRED


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
    res["required_source"] = dict(REQUIRED_SOURCE)
    return res


def render(res: dict) -> str:
    lines = [
        f"repo     : {REPO}",
        f"sha      : {res['sha']}",
        f"basis    : {res['basis']}",
        f"required : {res['required_source']['source']} -- {res['required_source']['detail']}",
        f"runs     : {res['total_runs']} check-runs on the answering sha",
    ]
    for name in REQUIRED:
        lines.append(f"  {name:<16} {res['contexts'].get(name, 'ABSENT')}")
    lines.append(f"VERDICT  : {res['verdict']} rc={res['rc']} -- {res['detail']}")
    return "\n".join(lines)


def protection_controls() -> int:
    """R4 for the REQUIRED resolver: drive classify_protection with every shape,
    including the ones that would silently break the verdict.

    `--self-test` alone would only ever exercise the happy path, because live
    branch protection is healthy today. An assertion never seen fail is unproven.
    """
    live_ok = {"contexts": ["capmap-check", "static-analysis", "smoke-ladder",
                            "frontend", "pytest", "no-hollow", "schema-prm", "referent-verify"]}
    cases = [
        ("live protection, matching the literal",
         live_ok, "branch_protection", True,
         lambda s, i: len(s) == 8 and not i["drift_vs_literal"]["added"]
                      and not i["drift_vs_literal"]["dropped"]),

        ("protection gained a context the literal never had -- THE BUG THIS FIXES",
         {"contexts": live_ok["contexts"] + ["licence-scan"]}, "branch_protection", True,
         lambda s, i: "licence-scan" in s and i["drift_vs_literal"]["added"] == ["licence-scan"]),

        ("protection dropped a context the literal still lists",
         {"contexts": [c for c in live_ok["contexts"] if c != "frontend"]},
         "branch_protection", True,
         lambda s, i: "frontend" not in s and i["drift_vs_literal"]["dropped"] == ["frontend"]),

        ("EMPTY contexts -- must NOT become a gate that can only go green",
         {"contexts": []}, "fallback_literal", False,
         lambda s, i: s == REQUIRED_FALLBACK),

        ("contexts key absent entirely",
         {"strict": False}, "fallback_literal", False,
         lambda s, i: s == REQUIRED_FALLBACK),

        ("API call failed (None) -- fall back, and say the set may be stale",
         None, "fallback_literal", False,
         lambda s, i: s == REQUIRED_FALLBACK and "UNMEASURED" in i["detail"]),
    ]
    bad = 0
    for why, payload, want_source, want_trusted, predicate in cases:
        got_set, info = classify_protection(payload)
        ok = (info["source"] == want_source
              and info["trusted"] is want_trusted
              and predicate(got_set, info))
        bad += 0 if ok else 1
        print(f"[{'ok  ' if ok else 'FAIL'}] {info['source']:<18} trusted={str(info['trusted']):<5} {why}")
        if not ok:
            print(f"        got set={got_set}")
            print(f"        got info={info}")
    print(f"protection controls: {len(cases) - bad}/{len(cases)} -- the EMPTY and FAILED "
          f"shapes are the ones that matter; both must refuse to become a verdict.")
    return 0 if bad == 0 else 1


def self_test(repo: str) -> int:
    """R4: prove each verdict on a REAL sha in this repo, including a red one.
    An assertion never seen RED is unproven, not passing."""
    rc_controls = protection_controls()
    print()
    resolve_required(repo)
    print(f"live REQUIRED: {REQUIRED_SOURCE['source']} -- {list(REQUIRED)}\n")
    cases = [
        # 2026-08-31: was ("GREEN", ...) -- the fixture aged out when branch
        # protection gained `referent-verify`; that context does not exist on this
        # 2026-era sha, and absent is not green, so the live set correctly answers
        # cannot-evaluate. GREEN-via-PR-head-redirect is still exercised by the
        # origin/main tip case below.
        ("UNKNOWN", "de06856006e06890d1634d182ff6f9a9b93af13e",
         "PR #2418 head -- 7 of the NOW-8 required contexts success; "
         "referent-verify absent by era"),
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
    return 0 if (bad == 0 and rc_controls == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sha")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--branch", default="main",
                    help="branch whose protection defines the required set")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return self_test(a.repo)
    if not a.sha:
        ap.error("--sha is required (or use --self-test)")

    resolve_required(a.repo, a.branch)
    res = judge(a.repo, a.sha)
    print(json.dumps(res, indent=2) if a.json else render(res))
    return res["rc"]


if __name__ == "__main__":
    sys.exit(main())
