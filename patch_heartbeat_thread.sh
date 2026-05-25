#!/usr/bin/env bash
# patch_heartbeat_thread.sh
# ------------------------------------------------------------------------------
# Adds a background heartbeat thread to sentinel_directive_generator.py so the
# 'last_heartbeat' column in service_health updates every 30s regardless of
# cycle state. Previously heartbeat() only fired once per 7200s cycle.
#
# Preserves the existing heartbeat() function and its call in run_cycle() —
# the thread is additive. If the thread dies silently, the in-cycle call is
# still a backstop every 2h.
#
# Safety: full backup, AST validation, auto-rollback on error, dry-run mode.
#
# Usage:
#   DRY_RUN=1 bash /home/workspace/zo_sentinel/patch_heartbeat_thread.sh  # preview
#   bash /home/workspace/zo_sentinel/patch_heartbeat_thread.sh            # apply
#   SKIP_RESTART=1 bash /home/workspace/zo_sentinel/patch_heartbeat_thread.sh
# ------------------------------------------------------------------------------

set -euo pipefail

SENTINEL_DIR="/home/workspace/zo_sentinel"
GEN="$SENTINEL_DIR/sentinel_directive_generator.py"
LOG="/home/workspace/logs/sentinel_directive_gen.log"
TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN="${DRY_RUN:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
HEARTBEAT_INTERVAL_SEC="${HEARTBEAT_INTERVAL_SEC:-30}"

RED=$'\033[91m'; GREEN=$'\033[92m'; YELLOW=$'\033[93m'; CYAN=$'\033[96m'; DIM=$'\033[2m'; RESET=$'\033[0m'
say()  { printf "%s[*]%s %s\n"  "$CYAN"   "$RESET" "$*"; }
ok()   { printf "%s[OK]%s %s\n" "$GREEN"  "$RESET" "$*"; }
warn() { printf "%s[!]%s %s\n"  "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[X]%s %s\n"  "$RED"    "$RESET" "$*"; exit 1; }
dry()  { [[ "$DRY_RUN" == "1" ]] && printf "%s[DRY]%s would: %s\n" "$DIM" "$RESET" "$*"; }

say "Heartbeat thread patcher"
say "timestamp: $TS  dry-run: $DRY_RUN  interval: ${HEARTBEAT_INTERVAL_SEC}s"
echo

[[ -f "$GEN" ]] || fail "Generator not found at $GEN"
python3 -c "import ast; ast.parse(open('$GEN').read())" \
  && ok "Pre-flight: generator parses cleanly" \
  || fail "Pre-flight: generator has syntax errors — aborting"

if grep -q '_heartbeat_thread' "$GEN" || grep -q 'heartbeat_loop' "$GEN"; then
  ok "Heartbeat thread already installed — nothing to do"
  exit 0
fi

GEN_PID="$(pgrep -f 'sentinel_directive_generator.py' | head -n1 || true)"
[[ -n "$GEN_PID" ]] && ok "Running PID: $GEN_PID" || { warn "No running generator"; SKIP_RESTART=1; }

BACKUP_DIR="$SENTINEL_DIR/.patch_backups/$TS"
if [[ "$DRY_RUN" == "0" ]]; then
  mkdir -p "$BACKUP_DIR"
  cp "$GEN" "$BACKUP_DIR/sentinel_directive_generator.py"
  ok "Backup -> $BACKUP_DIR"
else
  dry "backup -> $BACKUP_DIR"
fi

rollback() {
  local rc=$?
  if [[ $rc -ne 0 && "$DRY_RUN" == "0" && -f "$BACKUP_DIR/sentinel_directive_generator.py" ]]; then
    warn "Error rc=$rc — rolling back"
    cp "$BACKUP_DIR/sentinel_directive_generator.py" "$GEN"
    fail "Rolled back. Original at $BACKUP_DIR"
  fi
}
trap rollback ERR

# ------------------------------------------------------------------------------
# The patcher:
#   1. Add 'import threading' near the top if not present
#   2. Define heartbeat_loop() and _start_heartbeat_thread() below heartbeat()
#   3. Call _start_heartbeat_thread() at the top of run() before the first cycle
# ------------------------------------------------------------------------------
say "Rewriting generator via AST-safe Python patcher"

REWRITER="$(mktemp)"
cat > "$REWRITER" <<PYEOF
import re, sys, ast

gen_path = sys.argv[1]
interval = int(sys.argv[2])
src = open(gen_path).read()

# --- 1. Add 'import threading' if missing -------------------------------------
if 'import threading' not in src:
    # Add after the existing 'import os, json, time, logging, requests, hashlib' line
    m = re.search(r'^(import os, json, time, logging, requests, hashlib)$', src, re.MULTILINE)
    if not m:
        print('ERROR: could not find import anchor', file=sys.stderr); sys.exit(2)
    src = src[:m.end()] + '\nimport threading' + src[m.end():]
    print('added: import threading')
else:
    print('skip: threading already imported')

# --- 2. Insert heartbeat_loop + _start_heartbeat_thread after heartbeat() -----
anchor = re.search(
    r'(def heartbeat\(\):\n(?:    [^\n]*\n)+)',
    src
)
if not anchor:
    print('ERROR: could not find heartbeat() function', file=sys.stderr); sys.exit(3)

new_funcs = f'''
_HEARTBEAT_INTERVAL = {interval}
_heartbeat_thread = None

def _heartbeat_loop():
    """Background thread: heartbeat every _HEARTBEAT_INTERVAL seconds.
    Independent of cycle state so liveness monitors always see fresh rows."""
    while True:
        try:
            heartbeat()
        except Exception as e:
            log.debug("heartbeat thread tick failed: %s", e)
        time.sleep(_HEARTBEAT_INTERVAL)

def _start_heartbeat_thread():
    """Start the heartbeat thread as a daemon. Safe to call more than once."""
    global _heartbeat_thread
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="heartbeat", daemon=True
    )
    _heartbeat_thread.start()
    log.info("Heartbeat thread started (interval=%ds)", _HEARTBEAT_INTERVAL)

'''

src = src[:anchor.end()] + new_funcs + src[anchor.end():]
print('added: heartbeat_loop and _start_heartbeat_thread')

# --- 3. Call _start_heartbeat_thread() at top of run() ------------------------
# Anchor: the first log.info('=' * 60) inside run()
run_anchor = re.search(
    r'(def run\(\):\n)(    log\.info\("=" \* 60\))',
    src
)
if not run_anchor:
    print('ERROR: could not find run() log anchor', file=sys.stderr); sys.exit(4)

replacement = run_anchor.group(1) + '    _start_heartbeat_thread()\n' + run_anchor.group(2)
src = src[:run_anchor.start()] + replacement + src[run_anchor.end():]
print('added: _start_heartbeat_thread() call in run()')

# --- Validate ------------------------------------------------------------------
try:
    ast.parse(src)
except SyntaxError as e:
    print(f'ERROR: result has syntax error: {e}', file=sys.stderr); sys.exit(5)

open(gen_path, 'w').write(src)
print(f'ok: wrote {len(src)} bytes')
PYEOF

if [[ "$DRY_RUN" == "1" ]]; then
  dry "add threading import, heartbeat thread functions, and run() start call"
  rm -f "$REWRITER"
else
  python3 "$REWRITER" "$GEN" "$HEARTBEAT_INTERVAL_SEC" \
    && ok "Patcher completed" \
    || fail "Patcher failed"
  rm -f "$REWRITER"
  python3 -c "import ast; ast.parse(open('$GEN').read())" \
    && ok "Post-patch: syntax clean" \
    || fail "Post-patch: syntax broken"
fi

# ------------------------------------------------------------------------------
# Restart
# ------------------------------------------------------------------------------
echo
say "Restart generator so thread starts"

if [[ "$SKIP_RESTART" == "1" ]]; then
  warn "SKIP_RESTART=1 — restart manually:"
  echo "    kill $GEN_PID"
  echo "    nohup python3 $GEN >> $LOG 2>&1 &"
elif [[ "$DRY_RUN" == "1" ]]; then
  dry "kill $GEN_PID && restart via nohup"
else
  if [[ -n "${GEN_PID:-}" ]]; then
    kill "$GEN_PID" 2>/dev/null || true
    sleep 2
    kill -0 "$GEN_PID" 2>/dev/null && { kill -9 "$GEN_PID" || true; sleep 1; }
    ok "Killed old PID $GEN_PID"
  fi

  if [[ -f "$HOME/.zo_env" ]]; then
    set +u; source "$HOME/.zo_env"; set -u
    ok "Sourced ~/.zo_env"
  fi

  nohup python3 "$GEN" >> "$LOG" 2>&1 &
  NEW_PID=$!
  sleep 4
  if kill -0 "$NEW_PID" 2>/dev/null; then
    ok "Restarted — new PID $NEW_PID"
  else
    fail "Restart failed — check $LOG"
  fi
fi

# ------------------------------------------------------------------------------
# Verification
# ------------------------------------------------------------------------------
echo
say "Verification"

if [[ "$DRY_RUN" == "0" ]]; then
  grep -q 'Heartbeat thread started' "$LOG" \
    && ok "Log shows thread started" \
    || warn "Log doesn't yet show 'Heartbeat thread started' — give it a few seconds"

  if [[ "$SKIP_RESTART" == "0" ]]; then
    WAIT=$((HEARTBEAT_INTERVAL_SEC + 10))
    say "Waiting ${WAIT}s for two heartbeat ticks..."
    sleep "$WAIT"

    RESULT="$(curl -s -X POST http://127.0.0.1:8772/query \
      -H 'Content-Type: application/json' \
      -d '{"sql":"SELECT service, CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age_sec FROM service_health WHERE service='"'"'sentinel_directive_generator'"'"'"}' 2>/dev/null)"
    echo "$RESULT"
    AGE="$(echo "$RESULT" | grep -oE '"age_sec":[0-9]+' | head -1 | grep -oE '[0-9]+' || echo '999999')"

    if [[ "$AGE" -le $((HEARTBEAT_INTERVAL_SEC + 15)) ]]; then
      ok "Heartbeat age: ${AGE}s (expected ≤ $((HEARTBEAT_INTERVAL_SEC + 15))s) — thread is ticking"
    else
      warn "Heartbeat age: ${AGE}s — check log for errors"
    fi
  fi

  echo
  ok "Done. Backup: $BACKUP_DIR"
  echo
  say "Rollback if needed:"
  echo "    cp $BACKUP_DIR/sentinel_directive_generator.py $GEN"
  [[ "$SKIP_RESTART" == "0" ]] && echo "    pkill -f sentinel_directive_generator.py && nohup python3 $GEN >> $LOG 2>&1 &"
else
  echo
  ok "Dry-run complete. Run without DRY_RUN=1 to apply."
fi