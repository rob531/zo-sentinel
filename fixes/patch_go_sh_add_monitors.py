#!/usr/bin/env python3
"""patch_go_sh_add_monitors.py -- add section 12.8 to go.sh launching the two loop
monitors (loop_watch.py, tools/graph_refresh.py) under the ingestor-style direct-nohup
+ while-true crash-respawn pattern.

Why NOT daemon_wrapper.sh: every existing daemon_wrapper call is `<name> <script.py>`
with NO trailing args -- the wrapper does not forward args, so `--interval` would be
dropped and the monitor would run one-shot then stop. The publisher/governor blocks
(12.6b) show the correct pattern for arg-taking loop daemons: a direct
`nohup bash -c 'while true; do <cmd --args>; sleep N; done'`.

Also extends the zm-go kill-list so a restart cleans the old monitors first
(`pkill -f loop_watch.py` matches both the python child and the bash while-wrapper).

Idempotent (skips if section 12.8 present). Backs up + `bash -n` validates before writing.
"""
import shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")
MARKER = "12.8 Monitors"

ANCHOR_OLD = 'hdr "13. World Article Feeder"'
MONITORS = (
    'hdr "12.8 Monitors (loop_watch + graph_refresh -- self-looping, crash-respawn)"\n'
    '# loop_watch: read-only end-to-end loop watcher; emails on ALERT via /zo/notify.\n'
    '# graph_refresh: self-healing re-indexer (idle-gated; loads only on a HEAD change).\n'
    '# Both self-loop via --interval; the bash while-wrapper respawns on crash. We do NOT\n'
    '# use daemon_wrapper.sh here because it does not forward trailing args (--interval);\n'
    '# this is the same direct-nohup loop pattern the publisher/governor use (12.6b).\n'
    'nohup bash -c "while true; do python3 $SENTINEL/loop_watch.py --interval 1800; sleep 30; done" >> $LOGS/loop_watch.log 2>&1 &\n'
    'sleep 2\n'
    "LW=$(pgrep -f 'loop_watch.py' 2>/dev/null | head -1)\n"
    '[[ -n "$LW" ]] && ok "LoopWatch PID $LW" || warn "LoopWatch failed"\n'
    'nohup bash -c "while true; do python3 $SENTINEL/tools/graph_refresh.py --interval 900; sleep 30; done" >> $LOGS/graph_refresh.log 2>&1 &\n'
    'sleep 2\n'
    "GRF=$(pgrep -f 'graph_refresh.py' 2>/dev/null | head -1)\n"
    '[[ -n "$GRF" ]] && ok "GraphRefresh PID $GRF" || warn "GraphRefresh failed"\n'
    '\n'
)
ANCHOR_NEW = MONITORS + ANCHOR_OLD

KILL_OLD = "            liveness_probe.py signal_bridge.py ecosystems_metadata_fetcher.py \\"
KILL_NEW = "            liveness_probe.py loop_watch.py graph_refresh.py signal_bridge.py ecosystems_metadata_fetcher.py \\"


def _backup(p):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak.{ts}"); shutil.copy2(p, bak)
    print(f"  [backup] {bak.name}")

def main():
    if not TARGET.exists():
        print(f"[patch] {TARGET} not found", file=sys.stderr); return 1
    s = TARGET.read_text()
    if MARKER in s:
        print("[patch] section 12.8 already present -- idempotent no-op"); return 0
    if ANCHOR_OLD not in s:
        print(f"[patch] anchor not found: {ANCHOR_OLD!r}", file=sys.stderr); return 1
    new = s.replace(ANCHOR_OLD, ANCHOR_NEW, 1)
    if KILL_OLD in new:
        new = new.replace(KILL_OLD, KILL_NEW, 1)
    else:
        print("[patch] WARN: kill-list anchor not found; monitors will launch but not be "
              "killed on restart (broad pkill daemon_wrapper won't catch them). Continuing.")
    tmp = TARGET.with_suffix(".tmp_monitors"); tmp.write_text(new)
    chk = subprocess.run(["bash", "-n", str(tmp)], capture_output=True, text=True)
    if chk.returncode != 0:
        print("[patch] bash -n FAILED -- not writing:\n" + chk.stderr, file=sys.stderr)
        tmp.unlink(missing_ok=True); return 1
    _backup(TARGET); tmp.replace(TARGET)
    print("[patch] section 12.8 (monitors) added + kill-list extended; bash -n OK")
    print("[patch] start now without a full `zm go`:")
    print("        nohup bash -c 'while true; do python3 /home/workspace/zo_sentinel/loop_watch.py --interval 1800; sleep 30; done' >> /home/workspace/logs/loop_watch.log 2>&1 &")
    print("        nohup bash -c 'while true; do python3 /home/workspace/zo_sentinel/tools/graph_refresh.py --interval 900; sleep 30; done' >> /home/workspace/logs/graph_refresh.log 2>&1 &")
    return 0

if __name__ == "__main__":
    sys.exit(main())
