#!/usr/bin/env python3
"""
sweep_ghost_done.py -- clear the graveyard of GHOST .done sentinels.

goose_runner used to stamp `<id>.done.json` whenever the build PROCESS exited 0,
even when no file was produced (the pre-#23 502 era + the goose-success-without-
file bug). Those bogus sentinels permanently mark real product directives as
"already built", so the runner skips them forever ("non-eligible") and the
capability never gets built. build_completion.py now PREVENTS new ones; this tool
REMEDIATES the existing ones so the burned directives become eligible again (and,
with the ladder fixed, actually build on the next cycle).

A .done sentinel is GHOST when the directive's declared output file is absent on
disk. We read the output from the directive's source json (done/ or pending/) when
available; otherwise we infer it from a `build_<name>` id and require that NONE of
the plausible outputs exist before deleting -- conservative, so a real build is
never un-marked.

Usage (on the host):
    PYTHONPATH=/home/workspace/zo_sentinel python3 tools/sweep_ghost_done.py            # dry-run
    PYTHONPATH=/home/workspace/zo_sentinel python3 tools/sweep_ghost_done.py --apply    # delete the ghosts
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from zo_sentinel.build_completion import declared_output, output_present

HOME = Path("/home/workspace/zo_sentinel")
DIRECTIVES = HOME / "directives"
DONE_DIR = DIRECTIVES / "done"
PENDING_DIR = DIRECTIVES / "pending"

# build_<name> / gen_<hash>_build_<name>  ->  <name>
_BUILD_ID = re.compile(r"^(?:gen_[0-9a-f]+_)?build_(.+)$")
_INFER_EXTS = (".py", ".sh", ".md", ".html", ".jsx", ".sql", ".yaml", ".yml")


def _source_directive(directive_id: str) -> dict | None:
    """The directive json (with output_file), from done/ or pending/ if present."""
    for d in (DONE_DIR, PENDING_DIR):
        f = d / f"{directive_id}.json"
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def _is_ghost(directive_id: str) -> tuple[bool, str]:
    """(is_ghost, reason). Ghost = we can determine an expected output and it's
    absent. Unknown/ambiguous -> (False, ...) so we never delete a real build."""
    src = _source_directive(directive_id)
    if src is not None:
        out = declared_output(src, str(HOME))
        if out is None:
            return False, "source declares no output_file (goal/wire directive) -- keep"
        return (not output_present(out)), f"declared output {out.name} {'ABSENT' if not output_present(out) else 'present'}"
    # No source json: infer from a build_<name> id.
    m = _BUILD_ID.match(directive_id)
    if not m:
        return False, "no source json and id not 'build_<name>' -- can't verify, keep"
    name = m.group(1)
    candidates = [HOME / name] + [HOME / f"{name}{ext}" for ext in _INFER_EXTS]
    if any(output_present(c) for c in candidates):
        return False, f"inferred output for '{name}' exists -- keep"
    return True, f"inferred output for '{name}' ABSENT (tried {name}+{','.join(_INFER_EXTS)})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete the ghost sentinels (default: dry-run)")
    args = ap.parse_args(argv)

    if not DIRECTIVES.is_dir():
        print(f"ERROR: {DIRECTIVES} not found", file=sys.stderr)
        return 2

    done = sorted(DIRECTIVES.glob("*.done.json"))
    ghosts, kept = [], 0
    for sentinel in done:
        directive_id = sentinel.name[: -len(".done.json")]
        is_ghost, reason = _is_ghost(directive_id)
        if is_ghost:
            ghosts.append((sentinel, directive_id, reason))
        else:
            kept += 1

    print(f"scanned {len(done)} .done sentinels: {len(ghosts)} ghost, {kept} real/ambiguous (kept)\n")
    for sentinel, directive_id, reason in ghosts:
        print(f"  GHOST {directive_id}: {reason}")
        if args.apply:
            for victim in (sentinel, DIRECTIVES / f"{directive_id}.ghost.json"):
                try:
                    victim.unlink()
                except OSError:
                    pass
            print(f"        deleted {sentinel.name} (directive now eligible to rebuild)")

    if ghosts and not args.apply:
        print(f"\n[dry-run] {len(ghosts)} ghost sentinels would be removed. Re-run with --apply.")
    elif args.apply:
        print(f"\nremoved {len(ghosts)} ghost sentinels -- those directives will rebuild next goose cycle.")
    else:
        print("\nno ghost sentinels found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
