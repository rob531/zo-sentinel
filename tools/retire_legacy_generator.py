#!/usr/bin/env python3
r"""
retire_legacy_generator.py -- one-shot host patcher that RETIRES the legacy
sentinel_directive_generator.py daemon (the pre-Phase-0b MiniMax directive
generator).

Why: the Phase-0b goose architect (sentinel_directive_generator_goose.py, recipe
goose_recipes/directive_architect.yaml) supersedes it and is already
/app-product-scoped + enrichment-deprecated. The legacy generator's hardcoded
prompt still orders 'SIGNAL ENRICHMENT (highest-priority)', flooding
directives/proposed/ to its cap (40) and STARVING the goose architect
('proposed/ depth >= cap; skipping cycle'). Retiring it lets the product-scoped
architect propose again.

Host edit (go.sh section 12.5): stop launching sentinel_directive_generator.py.
It stays in the section-1 pkill list, so any straggler is reaped and NOT
relaunched. The Phase-0b sibling (12.5b, _goose) is left UNTOUCHED.

Optional:
  --kill            pkill the running legacy generator now (precise pattern
                    'sentinel_directive_generator\.py' -- never the _goose one),
                    instead of waiting for the next `zm go` reap.
  --drain-proposed  archive directives/proposed/*.json -> proposed/_retired_<ts>/
                    (REVERSIBLE move) to clear the cap so the goose architect
                    resumes immediately.

Usage (on ZoComputer):
    python3 retire_legacy_generator.py --dry-run               # report only
    python3 retire_legacy_generator.py                         # patch go.sh (.bak)
    python3 retire_legacy_generator.py --kill --drain-proposed # + stop now + clear cap

Idempotent + safe: skipped if already applied; if the go.sh anchor is not found
verbatim (version drift) it is skipped with a warning and nothing else is touched.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

GO_DEFAULT = "/home/workspace/zo_mesh/go.sh"
PROPOSED_DEFAULT = "/home/workspace/zo_sentinel/directives/proposed"

OLD_SEC = (
    'hdr "12.5 Sentinel Directive Generator"\n'
    "nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator $SENTINEL/sentinel_directive_generator.py >> $LOGS/sentinel_sentinel_directive_generator.log 2>&1 &\n"
    "sleep 2\n"
    "SDG=$(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | head -1)\n"
    '[[ -n "$SDG" ]] && ok "DirectiveGenerator PID $SDG" || warn "DirectiveGenerator failed"\n'
)
NEW_SEC = (
    'hdr "12.5 Sentinel Directive Generator (RETIRED -- superseded by 12.5b goose architect)"\n'
    "# Legacy MiniMax directive generator. Phase-0b's goose architect (12.5b,\n"
    "# sentinel_directive_generator_goose.py / directive_architect.yaml) supersedes it\n"
    "# and is /app-product-scoped. The legacy prompt still ordered enrichment work,\n"
    "# flooding proposed/ to cap and starving the goose architect. No longer launched.\n"
    "# It stays in the section-1 pkill list so any straggler is reaped (NOT relaunched).\n"
    'ok "DirectiveGenerator retired (live architect = 12.5b goose / directive_architect.yaml)"\n'
)
SEEN = "DirectiveGenerator retired"


def _patch_go(path: Path, dry: bool) -> str:
    src = path.read_text(encoding="utf-8")
    if SEEN in src:
        return "go.sh: already retired (skip)"
    if OLD_SEC not in src:
        return "go.sh: section-12.5 anchor NOT found verbatim -- skipped (version drift?)"
    out = src.replace(OLD_SEC, NEW_SEC, 1)
    if dry:
        return "go.sh: [dry-run] would remove the section-12.5 legacy-generator launch"
    path.with_suffix(path.suffix + ".bak").write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    return f"go.sh: section-12.5 launch removed (backup: {path.name}.bak)"


def _kill_legacy(dry: bool) -> str:
    pat = r"sentinel_directive_generator\.py"  # escaped dot -> never matches _goose.py
    if dry:
        return f"kill: [dry-run] would pkill -f '{pat}' (legacy only)"
    rc = subprocess.run(["pkill", "-f", pat]).returncode
    return f"kill: pkill -f '{pat}' rc={rc} (0=killed, 1=none running)"


def _drain_proposed(proposed: Path, dry: bool) -> str:
    if not proposed.is_dir():
        return f"drain: {proposed} not found -- skip"
    files = sorted(p for p in proposed.glob("*.json") if p.is_file())
    if not files:
        return "drain: proposed/ already empty -- skip"
    dest = proposed / f"_retired_{time.strftime('%Y%m%d_%H%M%S')}"
    if dry:
        return f"drain: [dry-run] would archive {len(files)} proposed file(s) -> {dest.name}/ (reversible)"
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(dest / f.name))
    return f"drain: archived {len(files)} proposed file(s) -> {dest.name}/ (restore: mv them back)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", default=GO_DEFAULT)
    ap.add_argument("--proposed", default=PROPOSED_DEFAULT)
    ap.add_argument("--kill", action="store_true")
    ap.add_argument("--drain-proposed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.go)
    if not p.exists():
        print(f"  WARN: go.sh not found at {p} -- skipped")
        rc = 2
    else:
        print("  " + _patch_go(p, args.dry_run))
        rc = 0
    if args.kill:
        print("  " + _kill_legacy(args.dry_run))
    if args.drain_proposed:
        print("  " + _drain_proposed(Path(args.proposed), args.dry_run))
    if not args.dry_run:
        print("Done. On the next `zm go` (or now, with --kill) the legacy generator stays down.")
        print("The goose architect (12.5b) becomes the single source of directives.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
