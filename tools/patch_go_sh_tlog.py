#!/usr/bin/env python3
"""
patch_go_sh_tlog.py -- host patcher for the LIVE zm-go launcher
(/home/workspace/zo_mesh/go.sh) that wires the telemetry session exporter.

Two idempotent edits:
  1. Add tlog_exporter.py to the section-1 pkill list (clean restart, no dups).
  2. Insert a "12.6c" block launching tools/tlog_exporter.py on :8788 (read-only,
     tailnet) right before "12.7 Liveness Probe".

The exporter serves the recorded script(1) sessions in $LOGS/tlog so the Tower
can pull them over the tailnet into its long-term SQLite memory store. Capture
itself is set up by tools/telemetry_capture_setup.py (zorec wrapper + rc hook);
tlog(1) is NOT used -- this Modal container can't give it an audit session id.

Usage (on ZoComputer):
    python3 tools/patch_go_sh_tlog.py            # patch in place (.bak written)
    python3 tools/patch_go_sh_tlog.py --dry-run  # report only
Then re-run `zm go` (or launch the 12.6c line) to start the exporter.

Idempotent + drift-safe: each edit is skipped if already present; a missing
anchor is skipped with a warning and nothing else is touched. Re-run after every
REFRESH_MODE=reset (it reverts host patches), like the other tools/ patchers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/go.sh"

# --- 1: add the exporter to the pkill list ----------------------------------
# Anchors on the promoter line that patch_go_sh.py's patch-1c produces.
OLD_KILL = "            zo_sentinel.promoters.proposed_to_pending_promoter \\\n"
NEW_KILL = ("            zo_sentinel.promoters.proposed_to_pending_promoter "
            "tlog_exporter.py \\\n")
SEEN_KILL = "tlog_exporter.py"

# --- 2: exporter launch block (inserted before 12.7 Liveness Probe) ---------
OLD_LP = 'hdr "12.7 Liveness Probe"\n'
TLOG_BLOCK = '''hdr "12.6c Telemetry Session Exporter :8788 (read-only, tailnet)"
# Serves recorded script(1) terminal sessions from $LOGS/tlog over the tailnet so
# the Tower can pull them into its long-term SQLite memory store. Read-only,
# GET-only, binds 0.0.0.0 (Modal does not expose it publicly; only tailnet peers
# reach it -- same posture as :8772/:8796). Capture is script(1) via the zorec
# wrapper / rc hook (tools/telemetry_capture_setup.py); tlog(1) is unusable here
# (no audit session id in this container). Set TLOG_EXPORT_TOKEN in $MESH/.zo_env
# to require an X-Tlog-Token header.
mkdir -p $LOGS/tlog
nohup python3 $SENTINEL/tools/tlog_exporter.py --dir $LOGS/tlog --port 8788 >> $LOGS/tlog_exporter.log 2>&1 &
sleep 1
TLX=$(pgrep -f 'tlog_exporter.py' 2>/dev/null | head -1)
[[ -n "$TLX" ]] && ok "TelemetryExporter PID $TLX (:8788 read-only)" || warn "TelemetryExporter failed"

'''
NEW_LP = TLOG_BLOCK + OLD_LP
SEEN_LP = "12.6c Telemetry Session Exporter"

PATCHES = [
    ("pkill-list exporter", SEEN_KILL, OLD_KILL, NEW_KILL),
    ("12.6c exporter launch", SEEN_LP, OLD_LP, NEW_LP),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    src = path.read_text(encoding="utf-8")

    out, applied, skipped = src, [], []
    for name, seen, old, new in PATCHES:
        if seen in out:
            skipped.append(f"{name} (already present)")
        elif old in out:
            out = out.replace(old, new, 1)
            applied.append(name)
        else:
            skipped.append(f"{name} (anchor NOT found -- version drift?)")

    for s in skipped:
        print(f"  skip: {s}")
    if not applied:
        print("Nothing to apply (already patched or anchors not found). No change.")
        return 0
    if args.dry_run:
        print(f"[dry-run] would apply: {applied}")
        return 0

    backup = path.with_suffix(path.suffix + ".tlogbak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Applied {applied} to {path} (backup: {backup})")
    print("Now run `zm go` (or the 12.6c line) to start the exporter on :8788.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
