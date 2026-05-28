#!/usr/bin/env bash
# patch_goose_runner_double_log.sh -- remove the redundant print() in
# /home/workspace/zo_sentinel/goose_runner.py's log() function so log lines
# stop appearing twice in /home/workspace/logs/goose_runner.log.
#
# The function currently writes to the log file AND prints to stdout.
# supervisord captures stdout into the same file, so every line lands twice
# with identical microsecond timestamps. This is the GH PR #7 fix that
# hasn't yet been bundled down to the tower.
#
# One-shot, idempotent: re-running on already-patched code is a no-op.

set -euo pipefail

GR=/home/workspace/zo_sentinel/goose_runner.py
TS=$(date -u +%Y%m%d_%H%M%SZ)
BAK="$GR.bak.double_log_fix.$TS"

echo "=== patch_goose_runner_double_log.sh ==="
echo "target: $GR"
echo "backup: $BAK"

if [[ ! -f "$GR" ]]; then
  echo "ERROR: $GR not found"
  exit 2
fi

if ! grep -q 'print(f"\[{ts}\] {msg}", flush=True)' "$GR"; then
  echo "Already patched (no print() in log()). Nothing to do."
  exit 0
fi

sudo cp -p "$GR" "$BAK"
echo "backup written"

sudo sed -i '/^    print(f"\[{ts}\] {msg}", flush=True)$/d' "$GR"

echo "--- verification ---"
awk '/^def log\(/{p=1} p; /^$/{if (p) exit}' "$GR" | head -8
echo

if grep -q 'print(f"\[{ts}\] {msg}", flush=True)' "$GR"; then
  echo "FAIL: print() still present"
  echo "Restoring from backup: $BAK"
  sudo cp -p "$BAK" "$GR"
  exit 3
fi

echo "  [OK] print() removed from log()"
echo
echo "=== done ==="
echo "To revert: sudo cp -p $BAK $GR"
echo
echo "Restart goose_runner so the new code is picked up:"
echo "  pkill -f 'python.* goose_runner.py$'"
echo "  (daemon_wrapper.sh respawns within ~5s)"
