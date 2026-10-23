"""undeclared_write_guard -- revert tracked-file writes a build agent never declared.

WHY THIS EXISTS (GH issue #3415, prevention fix 4; FU-349)
  2026-08-22 the goose tier-1 run of `scaffold_contract_init` resolved "the
  package __init__.py" to the repo's MAIN package and rewrote
  zo_sentinel/__init__.py into a broken import hub. ghost-guard rejected the
  directive -- but only because the DECLARED output was missing. The write it
  actually made was invisible to every gate. Every `python3 -m zo_sentinel.*`
  entrypoint would have died at the next restart.

WHAT IT DOES
  The runner snapshots the set of tracked-modified files BEFORE the agent
  subprocess and calls sweep() after it. Only files that BECAME dirty during
  the bracket are considered (the runner is serial; pre-existing dirt is never
  touched -- attribution before action). An offending file is:
    * a LOAD-BEARING MARKER (always -- their contract is a bare marker and the
      decomposer refuses directives that would target them), or
    * any tracked file, when the directive is build-class (declared_output is
      not None): a build-class directive's product is a NEW file at its
      declared path, so a tracked modification is undeclared by definition.
  Edit-class / goal-based directives (declared_output None) legitimately modify
  tracked files, so for them only the markers are gated.

  For each offending file: snapshot to _ghost_writes/<date>/ (forensics before
  destroy), then `git checkout HEAD -- <file>`, then verify the file is
  actually clean afterwards (the verify reads live git state, not the intent).

  FAIL-OPEN by construction: any git error, missing repo, or exception makes
  this a silent no-op. This guard must never be the reason a build stalls
  (R7: recovery over restriction -- it recovers files, it blocks nothing).
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Files no directive of ANY class may legitimately modify. Kept in sync with
# ops/host/package_marker_guard.py (the tick-time repairer for the same class).
LOAD_BEARING_MARKERS = frozenset({
    "zo_sentinel/__init__.py",
    "app/__init__.py",
})

FORENSICS_DIRNAME = "_ghost_writes"


def _git(repo_dir: Path, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=60,
            cwd=str(repo_dir))
    except Exception:
        return None


def tracked_dirty(repo_dir) -> Optional[set]:
    """Set of tracked files with worktree modifications (repo-relative posix
    paths), or None when git could not answer -- None means NO ATTRIBUTION
    BASIS and callers must treat it as 'do nothing', never as 'empty' (R6)."""
    r = _git(Path(repo_dir), "diff", "--name-only", "HEAD")
    if r is None or r.returncode != 0:
        return None
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def sweep(repo_dir, directive_id: str, dirty_before: Optional[Iterable[str]],
          declared_relpath: Optional[str] = None, log=None) -> list:
    """Revert undeclared tracked writes made during the runner's bracket.

    dirty_before: the tracked_dirty() snapshot taken before the agent ran.
    declared_relpath: repo-relative declared output for build-class directives,
      None for edit-class / goal-based (markers-only gating).
    Returns a list of dicts describing what was reverted (empty on no-op).
    Never raises."""
    actions = []
    try:
        repo = Path(repo_dir)
        if dirty_before is None:
            return actions          # no before-snapshot -> no attribution -> no-op
        after = tracked_dirty(repo)
        if after is None:
            return actions
        new_dirt = after - set(dirty_before)
        if not new_dirt:
            return actions
        build_class = declared_relpath is not None
        offending = {
            f for f in new_dirt
            if f in LOAD_BEARING_MARKERS
            or (build_class and f != declared_relpath)
        }
        if not offending:
            return actions
        stamp_day = datetime.now(timezone.utc).strftime("%Y%m%d")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        fdir = repo / FORENSICS_DIRNAME / stamp_day
        for f in sorted(offending):
            try:
                src = repo / f
                forensic = None
                if src.is_file():
                    fdir.mkdir(parents=True, exist_ok=True)
                    forensic = fdir / f"{f.replace('/', '__')}.{directive_id}.{stamp}"
                    shutil.copy2(src, forensic)
                r = _git(repo, "checkout", "HEAD", "--", f)
                still = tracked_dirty(repo)
                restored = (r is not None and r.returncode == 0
                            and still is not None and f not in still)
                actions.append({
                    "file": f, "directive_id": directive_id,
                    "forensics": str(forensic) if forensic else None,
                    "restored": restored, "ts": stamp,
                })
                if log:
                    log(f"[ghost-guard] {directive_id}: undeclared tracked write "
                        f"{'REVERTED' if restored else 'REVERT FAILED'}: {f} "
                        f"(forensics: {forensic})")
            except Exception as e:                     # pragma: no cover
                if log:
                    log(f"[ghost-guard] {directive_id}: sweep error on {f} "
                        f"(failing open): {e}")
    except Exception as e:                             # pragma: no cover
        if log:
            log(f"[ghost-guard] {directive_id}: sweep error (failing open): {e}")
    return actions
