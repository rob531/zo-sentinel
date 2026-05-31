#!/usr/bin/env python3
"""
retire_legacy_builder.py -- one-shot host patcher that RETIRES the legacy
zo_sentinel_builder.py daemon and repoints the `zm builder` shortcut at the
live builder's log.

Background: zo_sentinel_builder.py was the Tier-1 builder before goose_runner.
goose's loader now covers the same mesh `build_directive` rows
(load_directives_from_mesh Source 1), so the legacy daemon is fully redundant --
it only idempotently re-skipped the frozen rows, yet `zm builder` still tailed
ITS log, making the obvious "watch the builder" command show the dead path.

Two host-side edits (both in the zo_mesh repo):
  1. go.sh section 12  -- stop launching zo_sentinel_builder.py. It stays in the
     section-1 pkill list, so any straggler is reaped and NOT relaunched.
  2. zm_extra.zsh      -- repoint every `$LOGS/zo_sentinel_builder.log` ->
     `$LOGS/goose_runner.log` (covers `zm builder` AND `zm log builder`).

Usage (on ZoComputer):
    python3 retire_legacy_builder.py            # patch in place (.bak each)
    python3 retire_legacy_builder.py --dry-run  # report only
    python3 retire_legacy_builder.py --go /path/go.sh --zsh /path/zm_extra.zsh
Then re-source the shell (`source /home/workspace/zo_mesh/zm_extra.zsh`) and,
on the next `zm go`, the legacy builder will no longer start.

Idempotent + safe: each edit is skipped if already applied; if an anchor isn't
found verbatim (version drift) it is skipped with a warning and nothing else is
touched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

GO_DEFAULT = "/home/workspace/zo_mesh/go.sh"
ZSH_DEFAULT = "/home/workspace/zo_mesh/zm_extra.zsh"

# --- go.sh section 12: stop launching the legacy builder --------------------
OLD_SEC12 = (
    'hdr "12. ZO-SENTINEL Builder (nohup with env)"\n'
    "nohup bash $MESH/daemon_wrapper.sh zo_sentinel_builder "
    "$MESH/zo_sentinel_builder.py >> $LOGS/zo_sentinel_builder.log 2>&1 &\n"
    "sleep 3\n"
    "ZSB=$(pgrep -f 'zo_sentinel_builder.py' 2>/dev/null | head -1)\n"
    '[[ -n "$ZSB" ]] && ok "SentinelBuilder PID $ZSB" || warn "SentinelBuilder failed"\n'
)
NEW_SEC12 = (
    'hdr "12. ZO-SENTINEL Builder (RETIRED -- superseded by goose_runner 12.4b)"\n'
    "# zo_sentinel_builder.py was the Tier-1 builder before goose_runner. goose's\n"
    "# loader now covers the same mesh build_directive rows (load_directives_from_mesh\n"
    "# Source 1), so the legacy daemon is fully redundant -- it only idempotently\n"
    "# re-skipped the frozen rows. No longer launched here. It stays in the section-1\n"
    "# pkill list so any straggler is reaped (and NOT relaunched).\n"
    'ok "SentinelBuilder retired (live builder = goose_runner / section 12.4b)"\n'
)
SEEN_SEC12 = "SentinelBuilder retired"

# --- zm_extra.zsh: repoint the builder log alias(es) ------------------------
ZSH_OLD = "$LOGS/zo_sentinel_builder.log"
ZSH_NEW = "$LOGS/goose_runner.log"


def _patch_go(path: Path, dry: bool) -> str:
    src = path.read_text(encoding="utf-8")
    if SEEN_SEC12 in src:
        return "go.sh: already retired (skip)"
    if OLD_SEC12 not in src:
        return "go.sh: section-12 anchor NOT found verbatim -- skipped (version drift?)"
    out = src.replace(OLD_SEC12, NEW_SEC12, 1)
    if dry:
        return "go.sh: [dry-run] would remove the section-12 launch block"
    path.with_suffix(path.suffix + ".bak").write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    return f"go.sh: section-12 launch removed (backup: {path.name}.bak)"


def _patch_zsh(path: Path, dry: bool) -> str:
    src = path.read_text(encoding="utf-8")
    n = src.count(ZSH_OLD)
    if n == 0:
        return "zm_extra.zsh: no zo_sentinel_builder.log reference (already repointed / skip)"
    out = src.replace(ZSH_OLD, ZSH_NEW)
    if dry:
        return f"zm_extra.zsh: [dry-run] would repoint {n} builder-log reference(s) -> goose_runner.log"
    path.with_suffix(path.suffix + ".bak").write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    return f"zm_extra.zsh: repointed {n} builder-log reference(s) -> goose_runner.log (backup: {path.name}.bak)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", default=GO_DEFAULT)
    ap.add_argument("--zsh", default=ZSH_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rc = 0
    for label, p in (("go.sh", Path(args.go)), ("zm_extra.zsh", Path(args.zsh))):
        if not p.exists():
            print(f"  WARN: {label} not found at {p} -- skipped")
            rc = rc or 2
            continue
        fn = _patch_go if label == "go.sh" else _patch_zsh
        print("  " + fn(p, args.dry_run))

    if not args.dry_run:
        print("Done. Re-source the shell:  source /home/workspace/zo_mesh/zm_extra.zsh")
        print("Legacy builder will not start on the next `zm go`.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
