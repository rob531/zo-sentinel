#!/usr/bin/env python3
"""
telemetry_capture_setup.py -- host setup for terminal-session capture via
script(1) on the ZoComputer. Pairs with tools/tlog_exporter.py (serves the
sessions) and the Tower-side pull_sessions.py (ingests them).

tlog(1) is NOT used: this Modal container gives no audit session id, so tlog-rec
dies with "Failed retrieving session ID". script(1) (util-linux) needs no
session/install and is always present.

Installs three things (idempotent; re-run after every REFRESH_MODE=reset, which
reverts host changes):

  1. /home/workspace/bin/zorec        -- MANUAL wrapper: `zorec` starts a recorded
                                         shell. Lives on the persistent volume.
  2. /home/workspace/zo_mesh/zorec_autocapture.zsh
                                      -- AUTO-capture snippet (persistent). When
                                         sourced from an interactive zsh, it
                                         exec's `script` so EVERY shell records.
  3. ~/.zshrc source line             -- sources (2). This file is NOT on the
                                         persistent volume, so it resets on
                                         container restart -- which is exactly why
                                         this is re-run after a reset.

Guard: ZO_REC=1 is exported before exec, so the recorded inner shell (which
re-sources ~/.zshrc) does NOT re-trigger -> no capture loop. Non-interactive
shells and non-tty stdout are skipped.

Usage (on ZoComputer):
    python3 tools/telemetry_capture_setup.py            # apply
    python3 tools/telemetry_capture_setup.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

BIN = Path("/home/workspace/bin/zorec")
AUTOCAP = Path("/home/workspace/zo_mesh/zorec_autocapture.zsh")
TLOG_DIR = Path("/home/workspace/logs/tlog")
MARK_BEGIN = "# >>> zo telemetry auto-capture >>>"
MARK_END = "# <<< zo telemetry auto-capture <<<"

ZOREC = """#!/bin/sh
# zorec -- start a recorded interactive shell (script(1)). The session lands in
# the telemetry dir and is pulled to the Tower's long-term memory store via the
# :8788 exporter. Installed by tools/telemetry_capture_setup.py.
DIR="${ZOREC_DIR:-/home/workspace/logs/tlog}"
mkdir -p "$DIR"
[ -n "$ZO_REC" ] && exec "${SHELL:-/bin/zsh}"   # already recording -> plain shell
export ZO_REC=1
exec script -q -f "$DIR/sess-$(date +%Y%m%dT%H%M%S)-$$.log"
"""

AUTOCAP_BODY = """# zo telemetry auto-capture -- sourced from ~/.zshrc. Records every interactive
# shell via script(1); sessions are served by tlog_exporter.py (:8788) and pulled
# to the Tower. Installed by tools/telemetry_capture_setup.py.
export PATH="$PATH:/home/workspace/bin"
if [ -z "$ZO_REC" ] && [[ -o interactive ]] && [ -t 1 ] && command -v script >/dev/null 2>&1; then
  export ZO_REC=1
  mkdir -p /home/workspace/logs/tlog 2>/dev/null
  exec script -q -f "/home/workspace/logs/tlog/sess-$(date +%Y%m%dT%H%M%S)-$$.log"
fi
"""

RC_BLOCK = (f"{MARK_BEGIN}\n"
            f"[ -f {AUTOCAP} ] && source {AUTOCAP}\n"
            f"{MARK_END}\n")


def _write(path: Path, content: str, execable: bool, dry: bool, actions: list):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        actions.append(f"skip {path} (unchanged)")
        return
    actions.append(f"{'WOULD write' if dry else 'write'} {path}")
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if execable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zshrc", default=os.path.expanduser("~/.zshrc"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    dry = a.dry_run
    actions: list[str] = []

    # 1 + 2: persistent wrapper + autocapture snippet
    _write(BIN, ZOREC, True, dry, actions)
    _write(AUTOCAP, AUTOCAP_BODY, False, dry, actions)

    # capture dir
    if not TLOG_DIR.exists():
        actions.append(f"{'WOULD mkdir' if dry else 'mkdir'} {TLOG_DIR}")
        if not dry:
            TLOG_DIR.mkdir(parents=True, exist_ok=True)

    # 3: ~/.zshrc source line (the resettable part)
    rc = Path(a.zshrc)
    existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    if MARK_BEGIN in existing:
        actions.append(f"skip {rc} (auto-capture block already present)")
    else:
        actions.append(f"{'WOULD append' if dry else 'append'} auto-capture block to {rc}")
        if not dry:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            rc.write_text(existing + sep + "\n" + RC_BLOCK, encoding="utf-8")

    for line in actions:
        print(f"  {line}")
    if dry:
        print("[dry-run] no changes written.")
    else:
        print("\nDone. `zorec` is available now; auto-capture starts in NEW shells "
              "(open a fresh shell, or `source ~/.zshrc` -- it will exec into a "
              "recorded shell). Verify: ls -t /home/workspace/logs/tlog/ | head.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
