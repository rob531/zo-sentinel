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

  exit 0  SAFE   -- no path in the delta can reach the image; fire the later sha.
  exit 1  RESTAGE -- the delta touches the image surface; let the sentinel re-verify.
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


def changed_files(repo: str, staged: str, target: str) -> tuple[list[str], int, str]:
    out = _gh(["api", f"repos/{repo}/compare/{staged}...{target}", "--paginate"])
    # --paginate concatenates JSON documents; take the first and merge file lists.
    decoder, idx, files, total, head = json.JSONDecoder(), 0, [], 0, ""
    while idx < len(out):
        while idx < len(out) and out[idx].isspace():
            idx += 1
        if idx >= len(out):
            break
        doc, idx = decoder.raw_decode(out, idx)
        total = doc.get("total_commits", total) or total
        head = head or (doc.get("commits") or [{}])[-1].get("sha", "")
        files.extend(f["filename"] for f in doc.get("files") or [])
    return sorted(set(files)), total, head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", required=True, help="the vetted/staged SHA (40 chars)")
    ap.add_argument("--target", default="main")
    ap.add_argument("--repo", default="rob531/zo-sentinel")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        dockerfile = fetch_dockerfile(a.repo, a.staged)
        srcs = copy_sources(dockerfile)
        if not srcs:
            raise RuntimeError("parsed ZERO COPY sources -- refusing to call anything safe")
        files, prefixes = build_surface(srcs)
        changed, ncommits, head = changed_files(a.repo, a.staged, a.target)
    except Exception as exc:  # noqa: BLE001
        print(f"FIRE-GATE ERROR: {exc}", file=sys.stderr)
        print("verdict=ERROR -- do NOT read this as SAFE; re-run or let the sentinel re-verify.")
        return 2

    hits = [(p, why) for p in changed if (why := classify(p, files, prefixes))]
    verdict = "RESTAGE" if hits else "SAFE"

    if a.json:
        print(json.dumps({
            "verdict": verdict, "staged": a.staged, "target": a.target,
            "target_head": head, "commits_in_delta": ncommits,
            "files_in_delta": len(changed),
            "image_surface_hits": [{"path": p, "why": w} for p, w in hits],
            "copy_files": sorted(files), "copy_prefixes": sorted(prefixes),
        }, indent=2))
    else:
        print(f"staged   : {a.staged}")
        print(f"target   : {a.target} @ {head or '(no new commits)'}")
        print(f"delta    : {ncommits} commits, {len(changed)} files")
        print(f"surface  : {len(files)} COPYed files + {len(prefixes)} COPYed dirs "
              f"+ {len(ALWAYS_SENSITIVE)} contract paths")
        if hits:
            print(f"\nVERDICT: RESTAGE -- {len(hits)} path(s) in the delta reach the image:")
            for p, w in hits:
                print(f"  - {p}   [{w}]")
            print("\nThe staged evidence does NOT cover these. Let the next prod-drift-sentinel")
            print("run re-verify, or fire the ORIGINAL staged sha, which is still vetted.")
        else:
            print("\nVERDICT: SAFE -- nothing in the delta can reach the image.")
            print(f"Firing {a.target} builds a byte-equivalent image to the staged one.")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
