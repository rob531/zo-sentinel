#!/usr/bin/env python3
"""fire_gate.py -- is it still safe to fire a LATER sha on an EARLIER stage's evidence?

prod-drift-sentinel stages a one-click deploy at a vetted SHA. main keeps moving.
The chairman then wants to know one thing before firing: *would the image built
from today's main differ from the image that was actually verified?*

Until 2026-07-28 that question was answered by a prose rule --
"safe provided compare/<staged>...main is still services/staged-only".
That is a PROXY, and a bad one in both directions:

  * TOO STRICT: tools/, .github/, tests/, docs/ never enter the image. A CI-workflow
    fix landing on main tripped the rule and demanded a full re-verify for a change
    that cannot reach prod. (Observed 2026-07-28T16:51Z: PRs #2180/#2181.)
  * TOO LOOSE IN PRINCIPLE: it says nothing about *why* those paths are safe, so it
    cannot notice when the Dockerfile's COPY list changes underneath it.

This script answers the real question mechanically, and derives the answer FROM THE
DOCKERFILE AT THE STAGED SHA rather than from a hardcoded list, so it cannot go stale
when the COPY list changes.

  exit 0  SAFE   -- no path in the delta can reach the image AND the target sha is
                    not CI-red; fire the later sha.
  exit 1  RESTAGE -- the delta touches the image surface, OR the target sha is CI-RED
                    on the required contexts (sha_green.py); let the sentinel
                    re-verify. A CI-UNKNOWN target never produces this on its own.
  exit 2  ERROR  -- could not establish the answer. Never treat as SAFE.
                    (A probe that cannot evaluate is not a green.)

Usage:
    python tools/fire_gate.py --staged <40-char-sha> [--target main]
                             [--repo rob531/zo-sentinel] [--json]
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys

# Paths that change the built artifact or its runtime contract even though they are
# not themselves COPY sources. Kept deliberately short and justified:
#   Dockerfile     -- defines the COPY list itself
#   .dockerignore  -- silently subtracts from every COPY
#   fly.toml       -- release_command (alembic upgrade head), processes, health checks
#   services/active/ -- the promotion surface; app/_spine_generated.py is built from it,
#                     so a change here is intent-drift even before the generated file moves.
#                     This is the FU-102/v64 class (7 modules imported, none COPYed).
ALWAYS_SENSITIVE = ("Dockerfile", ".dockerignore", "fly.toml", "services/active/")

_SHA40 = re.compile(r"[0-9a-f]{40}")

# Explicitly NOT sensitive: services/staged/ is the builder's scratch surface. Nothing
# there is copied and nothing there is imported until a promotion moves it to active.
EXPLICITLY_INERT = ("services/staged/",)


def _gh(args: list[str]) -> str:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed rc={proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc.stdout


def fetch_dockerfile(repo: str, sha: str) -> str:
    raw = _gh(["api", f"repos/{repo}/contents/Dockerfile?ref={sha}", "--jq", ".content"])
    b64 = "".join(raw.split())
    if not b64:
        raise RuntimeError(f"empty Dockerfile at {sha}")
    return base64.b64decode(b64).decode("utf-8", "replace")


def copy_sources(dockerfile: str) -> list[str]:
    """Every source operand of every COPY, honouring backslash line continuations.

    `COPY --from=x` stages are ignored for source purposes: their inputs come from a
    previous build stage, not from the git tree, so a repo path cannot change them.
    """
    # splice continuations
    spliced = re.sub(r"\\\s*\n\s*", " ", dockerfile)
    sources: list[str] = []
    for line in spliced.splitlines():
        s = line.strip()
        if not re.match(r"(?i)^copy\b", s):
            continue
        toks = s.split()[1:]
        if any(t.startswith("--from=") for t in toks):
            continue
        toks = [t for t in toks if not t.startswith("--")]
        if len(toks) < 2:
            continue
        sources.extend(toks[:-1])  # last operand is the destination
    return sources


def build_surface(sources: list[str]) -> tuple[set[str], set[str]]:
    """Split COPY sources into exact-file matches and directory prefixes."""
    files: set[str] = set()
    prefixes: set[str] = set()
    for src in sources:
        p = src.lstrip("./").rstrip("/")
        if not p or p == ".":
            prefixes.add("")  # COPY . -- the whole tree is the surface
            continue
        if "." in p.rsplit("/", 1)[-1]:
            files.add(p)  # looks like a file
        else:
            prefixes.add(p + "/")
    return files, prefixes


def classify(path: str, files: set[str], prefixes: set[str]) -> str | None:
    for inert in EXPLICITLY_INERT:
        if path.startswith(inert):
            return None
    if path in files:
        return "COPY (exact file)"
    for pre in sorted(prefixes, key=len, reverse=True):
        if pre == "" or path.startswith(pre):
            return f"COPY (dir {pre or '.'})"
    for sensitive in ALWAYS_SENSITIVE:
        if path == sensitive or path.startswith(sensitive):
            return f"build/runtime contract ({sensitive})"
    return None


# GitHub caps compare responses at 300 files. Past that the `files` array is SILENTLY
# TRUNCATED -- the response still looks well-formed and still returns 200. A surface
# audit computed from a truncated file list would return SAFE while never having seen
# the path that mattered, and its exit code is indistinguishable from a real SAFE. So
# a truncated API answer is never a verdict. (2026-07-29, FU-160.)
#
# THAT GUARD FIRED FOR REAL on 2026-08-01T10:52Z: staged ae71dafd vs main 0905b4d4 was
# 325 files, of which the API showed 300. The cap truncates in SORTED order, so the 25
# it hid were the alphabetic tail -- `threat_intel_ingestor.py`, `tools/*`, `zo_sentinel/*`.
#
# Read the outcome carefully, because it is the opposite of the intuitive one. Audited
# uncapped, all 325 are OFF the COPY surface and the verdict is SAFE -- the SAME verdict
# the truncated list would have produced. So the guard did not save a wrong answer this
# time. It is still right, and this is the whole point: the truncated read would have
# been correct BY LUCK, and nothing in its output distinguishes lucky from sound. The
# hidden 25 sit in the alphabetic tail, which is exactly where a new root-level module
# would land -- `threat_intel_ingestor.py` LOOKS copyable and simply is not in this
# Dockerfile's 45-file COPY list. Next time the tail could hold one that is.
#
# But erroring is a RESTRICTION, and the question is answerable: `git diff --name-only
# A...B` in a local clone has no cap and uses the same three-dot merge-base semantics
# as the compare API. So the cap is now a SOURCE SWITCH, not a dead end (R7, prefer
# RECOVERY over RESTRICTION):
#
#     API answers  -> use it        (files_source="github-compare")
#     API capped   -> use local git (files_source="local-git")
#     both fail    -> ERROR exit 2  (unchanged; a probe that cannot evaluate is not a green)
#
# The ERROR path is deliberately KEPT REACHABLE -- when no local clone holds both
# objects there is still no answer, and inventing one is the failure this guards.
# Every verdict now NAMES which source answered, in both output modes, so a future
# widening of the fallback can never be mistaken for a silencing (FU-218's lesson:
# an audit must say which store answered).
COMPARE_FILES_CAP = 300


def resolve_head(repo: str, target: str) -> str:
    """Resolve --target to a concrete 40-char sha BEFORE comparing against it.

    Resolving first, then comparing against the resolved sha, closes two defects:

      * STALE REPORTED HEAD. The previous implementation read the head off the LAST
        COMMIT OF PAGE ONE of the paginated compare. Compare pages commits 100 at a
        time, so any delta over 100 commits reported the 100th commit as "the target
        head". Measured 2026-07-29T04:4xZ on a 124-commit delta: page 1 ended at
        c1d9917e (01:20Z) while main was actually 77fd0b1b (02:38Z). The VERDICT was
        unaffected -- the files union across pages is complete -- but every artifact
        recording "target main @ <sha>" recorded a commit that was not the head, and
        that sha is the evidence a human reads before firing prod.
      * TOCTOU. Naming and judging the same sha means the verdict still refers to a
        real tree even if main advances mid-run.
    """
    sha = _gh(["api", f"repos/{repo}/commits/{target}", "--jq", ".sha"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"could not resolve --target {target!r} to a sha (got {sha!r})")
    return sha


def _git(repo_path: str, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git -C {repo_path} {' '.join(args)} failed rc={proc.returncode}: "
            f"{proc.stderr.strip()[:400]}"
        )
    return proc.stdout


def _default_repo_path() -> str:
    """The clone this script itself lives in -- tools/fire_gate.py -> repo root."""
    return str(pathlib.Path(__file__).resolve().parent.parent)


def changed_files_git(repo_path: str, staged: str, target_sha: str) -> tuple[list[str], int]:
    """Uncapped file list from a local clone.

    `git diff --name-only A...B` is THREE-DOT (diff against the merge base), which is
    the same semantics GitHub's `compare/A...B` uses -- so this is a drop-in answer to
    the same question, not a different one. Two-dot would silently answer a different
    question whenever the branches had diverged.

    Both objects must already be present. This deliberately does NOT fetch: a gate that
    mutates the tree it is reading is how a shared worktree gets re-pointed underneath a
    sibling lane. If an object is missing, that is an ERROR for the caller to resolve.
    """
    for sha in (staged, target_sha):
        _git(repo_path, ["cat-file", "-e", f"{sha}^{{commit}}"])
    out = _git(repo_path, ["diff", "--name-only", f"{staged}...{target_sha}"])
    uniq = sorted({ln.strip() for ln in out.splitlines() if ln.strip()})
    n = _git(repo_path, ["rev-list", "--count", f"{staged}..{target_sha}"]).strip()
    return uniq, int(n or 0)


def changed_files(
    repo: str, staged: str, target_sha: str, repo_path: str | None = None
) -> tuple[list[str], int, str]:
    """Returns (files, commit_count, source). `source` is part of the verdict."""
    out = _gh(["api", f"repos/{repo}/compare/{staged}...{target_sha}", "--paginate"])
    # --paginate concatenates JSON documents; merge the file lists across ALL of them.
    decoder, idx, files, total = json.JSONDecoder(), 0, [], 0
    while idx < len(out):
        while idx < len(out) and out[idx].isspace():
            idx += 1
        if idx >= len(out):
            break
        doc, idx = decoder.raw_decode(out, idx)
        total = doc.get("total_commits", total) or total
        files.extend(f["filename"] for f in doc.get("files") or [])
    uniq = sorted(set(files))
    if len(uniq) < COMPARE_FILES_CAP:
        return uniq, total, "github-compare"

    # Capped -> the API cannot answer. Measured 2026-08-01: paging the compare endpoint
    # does NOT lift this. per_page=100 returned 300 files on page 1 and a degenerate
    # 1-file array on pages 2..6, for a distinct total of exactly 300 against a true
    # 325. So pagination is not the fix; a local clone is.
    capped_msg = (
        f"compare returned {len(uniq)} files, at or over the GitHub cap of "
        f"{COMPARE_FILES_CAP} -- the API file list is truncated"
    )
    path = repo_path or _default_repo_path()
    try:
        g_files, g_total = changed_files_git(path, staged, target_sha)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{capped_msg}, and the local fallback at {path} could not answer either "
            f"({exc}). The image surface cannot be audited. Re-stage at a newer sha "
            f"rather than firing."
        ) from exc
    if len(g_files) < len(uniq):
        # A fallback that sees LESS than the truncated API answer is not a fallback.
        raise RuntimeError(
            f"{capped_msg}, and the local clone at {path} returned only "
            f"{len(g_files)} files -- fewer than the truncated API list. Refusing to "
            f"audit on the smaller of two incomplete answers."
        )
    return g_files, g_total or total, f"local-git ({path})"


# --------------------------------------------------------------------------- CI
# THE TARGET SHA'S CI VERDICT. Until 2026-08-05 this question was asked in PROSE
# only: ops/host/deploy_prod.ps1 `.PARAMETER Sha` says "Must be a CI-green
# origin/main commit", and nothing anywhere executed that sentence. `sha_green.py`
# was written for precisely this question after the 2026-07-30T19:2xZ incident in
# which the ad-hoc greenness query was pointed at /commits/<sha>/status -- a
# surface on which all 7 required contexts read ABSENT, with nothing erroring --
# and then it sat at 20,406 bytes with ZERO callers until the improvement loop's
# dark-tool census selected it (cycle-0006). A precondition that lives only in a
# docstring is exactly the failure this apparatus exists to remove.
#
# This is NOT a new gate. It is the EXISTING precondition, moved out of a sentence
# and into the code that computes the fire decision. And it is deliberately
# ASYMMETRIC, because a symmetric one would be a gate that can stall the pipe:
#
#   sha_green RED (rc=1)     -> RESTAGE. Firing a CI-red sha was never permitted,
#                               by any reading, so this forecloses nothing that was
#                               previously allowed.
#   sha_green UNKNOWN (rc=2) -> VERDICT UNCHANGED, reported loudly. Unknown is not
#                               red (R6). An instrument that cannot answer must not
#                               convert into a blocker (R7) -- that is how gates
#                               that "can only go red" get built, and this repo has
#                               paid for several.
#   sha_green unavailable    -> identical to UNKNOWN. Never a block, never a silent
#                               pass, and it always says which of the two it was.
#
# --no-ci exists so the delta question stays answerable when GitHub is unreachable:
# recovery over restriction. It prints that it was used; a skip is never a pass.


def target_ci(repo: str, sha: str, branch: str = "main") -> dict:
    """Ask sha_green whether `sha` is green on the REQUIRED contexts.

    Never raises: an exception here would turn an advisory signal into an outage of
    the delta check, which is the opposite of the point.
    """
    try:
        here = str(pathlib.Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from sha_green import judge, resolve_required
        resolve_required(repo, branch)
        res = judge(repo, sha)
        return {"verdict": res["verdict"], "rc": res["rc"],
                "detail": str(res.get("detail", ""))[:400], "source": "sha_green.judge"}
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "UNKNOWN", "rc": 2,
                "detail": f"sha_green could not be consulted: {exc}",
                "source": "unavailable"}


def apply_ci(verdict: str, ci: dict) -> tuple[str, bool]:
    """Fold the CI verdict into the delta verdict. Pure, so it is testable without
    a network, and so the asymmetry above is one readable expression rather than a
    condition scattered through main()."""
    if verdict == "SAFE" and ci.get("rc") == 1:
        return "RESTAGE", True
    return verdict, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", required=True, help="the vetted/staged SHA (40 chars)")
    ap.add_argument("--target", default="main")
    ap.add_argument("--repo", default="rob531/zo-sentinel")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-ci", action="store_true", dest="no_ci",
                    help="skip the target sha's CI verdict (sha_green). The\n"
                         "delta question stays answerable when GitHub is\n"
                         "unreachable; the output always says the check was skipped.")
    ap.add_argument(
        "--repo-path", default=None, dest="repo_path",
        help="local clone used ONLY when the compare API caps out; "
             "defaults to the clone this script lives in",
    )
    a = ap.parse_args()

    try:
        dockerfile = fetch_dockerfile(a.repo, a.staged)
        srcs = copy_sources(dockerfile)
        if not srcs:
            raise RuntimeError("parsed ZERO COPY sources -- refusing to call anything safe")
        files, prefixes = build_surface(srcs)
        head = resolve_head(a.repo, a.target)
        changed, ncommits, src = changed_files(a.repo, a.staged, head, a.repo_path)
    except Exception as exc:  # noqa: BLE001
        print(f"FIRE-GATE ERROR: {exc}", file=sys.stderr)
        print("verdict=ERROR -- do NOT read this as SAFE; re-run or let the sentinel re-verify.")
        return 2

    hits = [(p, why) for p in changed if (why := classify(p, files, prefixes))]
    verdict = "RESTAGE" if hits else "SAFE"

    if a.no_ci:
        ci = {"verdict": "SKIPPED", "rc": None, "source": "skipped",
              "detail": "--no-ci: the target sha's CI state was NOT consulted"}
    else:
        ci = target_ci(a.repo, head, a.target if not _SHA40.fullmatch(a.target)
                       else "main")
    verdict, ci_forced = apply_ci(verdict, ci)

    if a.json:
        print(json.dumps({
            "verdict": verdict, "staged": a.staged, "target": a.target,
            "target_head": head, "commits_in_delta": ncommits,
            "files_in_delta": len(changed), "files_source": src,
            "image_surface_hits": [{"path": p, "why": w} for p, w in hits],
            "copy_files": sorted(files), "copy_prefixes": sorted(prefixes),
            "target_ci": ci, "restaged_by_ci": ci_forced,
        }, indent=2))
    else:
        print(f"staged   : {a.staged}")
        print(f"target   : {a.target} @ {head}")
        print(f"delta    : {ncommits} commits, {len(changed)} files")
        print(f"filesrc  : {src}")
        print(f"surface  : {len(files)} COPYed files + {len(prefixes)} COPYed dirs "
              f"+ {len(ALWAYS_SENSITIVE)} contract paths")
        print(f"target CI: {ci['verdict']} (rc={ci['rc']}) via {ci['source']}")
        if ci["verdict"] not in ("GREEN", "SKIPPED"):
            print(f"           {ci['detail'][:200]}")
        if ci["verdict"] == "UNKNOWN":
            print("           UNKNOWN IS NOT RED and it is not green: the CI state\n"
                  "           could not be established, so it has NOT changed the\n"
                  "           verdict below. Establish it before firing.")
        if a.staged == head:
            print("note     : staged == target head, so the delta is empty BY\n"
                  "           CONSTRUCTION and a SAFE below is tautological -- the\n"
                  "           CI line above is the only real signal in this run.")
        if hits:
            print(f"\nVERDICT: RESTAGE -- {len(hits)} path(s) in the delta reach the image:")
            for p, w in hits:
                print(f"  - {p}   [{w}]")
            print("\nThe staged evidence does NOT cover these. Let the next prod-drift-sentinel")
            print("run re-verify, or fire the ORIGINAL staged sha, which is still vetted.")
        elif ci_forced:
            print("\nVERDICT: RESTAGE -- the delta is image-inert, but the TARGET SHA\n"
                  "IS CI-RED on the required contexts. A byte-equivalent image built\n"
                  "from a red sha is still a red sha; deploy_prod.ps1 has always\n"
                  "required a CI-green commit. Fire the ORIGINAL staged sha, which is\n"
                  "still vetted, or wait for main to go green.")
        else:
            print("\nVERDICT: SAFE -- nothing in the delta can reach the image.")
            print(f"Firing {a.target} builds a byte-equivalent image to the staged one.")
            if ci["verdict"] == "GREEN":
                print("The target sha is also CI-GREEN on the required contexts.")
    return 1 if verdict == "RESTAGE" else 0


if __name__ == "__main__":
    sys.exit(main())
