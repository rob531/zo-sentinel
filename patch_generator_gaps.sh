#!/usr/bin/env bash
# patch_generator_gaps.sh
# ------------------------------------------------------------------------------
# Patches the three outstanding gaps in sentinel_directive_generator.py:
#   A) ALREADY_BUILT Python set is out of sync with SENTINEL_DIRECTIVE_SCHEMA.md
#   B) Live PID 31078 started before heartbeat() existed -- restart to activate
#   C) Generator doesn't refresh DB schema doc on cycle start
#
# Safety:
#   - Full backup of every modified file (.bak.<timestamp>)
#   - AST syntax validation after each Python edit
#   - Automatic rollback if anything fails
#   - Dry-run mode: DRY_RUN=1 ./patch_generator_gaps.sh
#
# Usage:
#   bash /home/workspace/zo_sentinel/patch_generator_gaps.sh           # apply
#   DRY_RUN=1 bash /home/workspace/zo_sentinel/patch_generator_gaps.sh # preview
#   SKIP_RESTART=1 bash /home/workspace/zo_sentinel/patch_generator_gaps.sh  # patch only, no restart
# ------------------------------------------------------------------------------

set -euo pipefail

SENTINEL_DIR="/home/workspace/zo_sentinel"
GEN="$SENTINEL_DIR/sentinel_directive_generator.py"
SCHEMA_MD="$SENTINEL_DIR/SENTINEL_DIRECTIVE_SCHEMA.md"
REFRESH_SCRIPT="$SENTINEL_DIR/refresh_schema_doc.py"
LOG="/home/workspace/logs/sentinel_directive_gen.log"
TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN="${DRY_RUN:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"

# Color helpers
RED=$'\033[91m'; GREEN=$'\033[92m'; YELLOW=$'\033[93m'; CYAN=$'\033[96m'; DIM=$'\033[2m'; RESET=$'\033[0m'

say()  { printf "%s[*]%s %s\n" "$CYAN"   "$RESET" "$*"; }
ok()   { printf "%s[OK]%s %s\n"  "$GREEN"  "$RESET" "$*"; }
warn() { printf "%s[!]%s %s\n"  "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[X]%s %s\n"  "$RED"    "$RESET" "$*"; exit 1; }
dry()  { [[ "$DRY_RUN" == "1" ]] && printf "%s[DRY]%s would: %s\n" "$DIM" "$RESET" "$*"; }

# ------------------------------------------------------------------------------
say "ZO-SENTINEL generator gap patcher"
say "timestamp: $TS  dry-run: $DRY_RUN  skip-restart: $SKIP_RESTART"
echo

# Sanity checks ----------------------------------------------------------------
[[ -f "$GEN"            ]] || fail "Generator not found at $GEN"
[[ -f "$SCHEMA_MD"      ]] || fail "Schema doc not found at $SCHEMA_MD"
[[ -f "$REFRESH_SCRIPT" ]] || fail "refresh_schema_doc.py not found at $REFRESH_SCRIPT"

python3 -c "import ast; ast.parse(open('$GEN').read())" \
  && ok "Pre-flight: generator parses cleanly" \
  || fail "Pre-flight: generator already has syntax errors -- aborting"

# Extract current PID of running generator
GEN_PID="$(pgrep -f 'sentinel_directive_generator.py' | head -n1 || true)"
if [[ -n "$GEN_PID" ]]; then
  ok "Found running generator at PID $GEN_PID"
else
  warn "No running generator detected (SKIP_RESTART will be forced)"
  SKIP_RESTART=1
fi

# Backup -----------------------------------------------------------------------
BACKUP_DIR="$SENTINEL_DIR/.patch_backups/$TS"
if [[ "$DRY_RUN" == "0" ]]; then
  mkdir -p "$BACKUP_DIR"
  cp "$GEN"       "$BACKUP_DIR/sentinel_directive_generator.py"
  ok "Backup -> $BACKUP_DIR"
else
  dry "mkdir -p $BACKUP_DIR && cp $GEN $BACKUP_DIR/"
fi

# ------------------------------------------------------------------------------
# Rollback handler: on ANY error from here on, restore the backup
# ------------------------------------------------------------------------------
rollback() {
  local rc=$?
  if [[ $rc -ne 0 && "$DRY_RUN" == "0" && -f "$BACKUP_DIR/sentinel_directive_generator.py" ]]; then
    warn "Error detected (rc=$rc) -- rolling back generator from $BACKUP_DIR"
    cp "$BACKUP_DIR/sentinel_directive_generator.py" "$GEN"
    fail "Rolled back. No changes applied. Inspect $BACKUP_DIR for the original."
  fi
}
trap rollback ERR

# ==============================================================================
# GAP A: Sync ALREADY_BUILT set with SENTINEL_DIRECTIVE_SCHEMA.md
# ==============================================================================
say "Gap A: syncing ALREADY_BUILT from schema markdown"

# Extract .py and .html filenames between 'Already Built' and next '##' header
MD_FILES="$(awk '
  /^## Already Built/        { grab=1; next }
  grab && /^## /             { grab=0 }
  grab                       { print }
' "$SCHEMA_MD" | grep -oE '[a-zA-Z0-9_]+\.(py|html)' | sort -u)"

COUNT="$(echo "$MD_FILES" | grep -c .)"
[[ "$COUNT" -lt 10 ]] && fail "Extracted only $COUNT files from schema -- parser broken, aborting"
ok "Extracted $COUNT built files from schema markdown"

# Build the replacement Python set literal
PY_SET="$(echo "$MD_FILES" | awk '{printf "\"%s\", ", $0}' | sed 's/, $//')"

# Write a rewriter via Python -- safer than sed for multi-line blocks
REWRITER="$(mktemp)"
cat > "$REWRITER" <<PYEOF
import re, sys, ast

gen_path   = sys.argv[1]
new_set    = sys.argv[2]
refresh_py = sys.argv[3]

src = open(gen_path).read()

# --- Gap A: replace ALREADY_BUILT = { ... } block -----------------------------
# Match 'ALREADY_BUILT = {' through the matching '}' at end of same block.
# Non-greedy across newlines, anchored to a line starting with '}'.
pattern = re.compile(
    r'ALREADY_BUILT\s*=\s*\{[^{}]*?\n\}',
    re.DOTALL
)
replacement = f'ALREADY_BUILT = {{{new_set}}}'
new_src, n = pattern.subn(replacement, src, count=1)
if n != 1:
    print('ERROR: could not locate ALREADY_BUILT block (matched %d times)' % n, file=sys.stderr)
    sys.exit(2)

# --- Gap C: inject refresh_schema_doc subprocess call at top of run_cycle() ---
# Only inject if not already present.
if 'refresh_schema_doc' not in new_src:
    # Find: def run_cycle():\n    heartbeat()
    # Insert the subprocess call between them.
    inject_pattern = re.compile(
        r'(def run_cycle\(\):\n)(    heartbeat\(\))'
    )
    inject_code = (
        r'\1'
        r'    # Refresh DB schema doc so directives always reflect live DB (added 2026-04-16)\n'
        r'    try:\n'
        r'        import subprocess\n'
        r'        subprocess.run(\n'
        r'            ["python3", "' + refresh_py + r'"],\n'
        r'            timeout=20, capture_output=True, check=False\n'
        r'        )\n'
        r'    except Exception as e:\n'
        r'        log.warning("schema refresh failed: %s", e)\n'
        r'\2'
    )
    new_src2, m = inject_pattern.subn(inject_code, new_src, count=1)
    if m != 1:
        print('ERROR: could not inject refresh call (matched %d times)' % m, file=sys.stderr)
        sys.exit(3)
    new_src = new_src2
    print('injected: refresh_schema_doc subprocess call')
else:
    print('skip: refresh_schema_doc already referenced')

# --- Validate with AST before writing -----------------------------------------
try:
    ast.parse(new_src)
except SyntaxError as e:
    print(f'ERROR: patched file has syntax error: {e}', file=sys.stderr)
    sys.exit(4)

open(gen_path, 'w').write(new_src)
print(f'ok: wrote {len(new_src)} bytes to {gen_path}')
PYEOF

if [[ "$DRY_RUN" == "1" ]]; then
  dry "rewrite ALREADY_BUILT with $COUNT entries and inject refresh_schema_doc call"
  rm -f "$REWRITER"
else
  python3 "$REWRITER" "$GEN" "$PY_SET" "$REFRESH_SCRIPT" \
    && ok "Patched generator (Gaps A + C)" \
    || fail "Rewriter failed"
  rm -f "$REWRITER"
  # Re-validate after write
  python3 -c "import ast; ast.parse(open('$GEN').read())" \
    && ok "Post-patch: generator parses cleanly" \
    || fail "Post-patch: generator broken -- rollback triggered"
fi

# ==============================================================================
# GAP B: Restart the generator so heartbeat() (already in source) actually fires
# ==============================================================================
say "Gap B: restart generator so heartbeat activates"

if [[ "$SKIP_RESTART" == "1" ]]; then
  warn "SKIP_RESTART=1 -- not restarting. Run manually when ready:"
  echo "    kill $GEN_PID"
  echo "    nohup python3 $GEN >> $LOG 2>&1 &"
elif [[ "$DRY_RUN" == "1" ]]; then
  dry "kill $GEN_PID && restart via nohup"
else
  if [[ -n "${GEN_PID:-}" ]]; then
    kill "$GEN_PID" 2>/dev/null || true
    sleep 2
    # Verify it's gone
    if kill -0 "$GEN_PID" 2>/dev/null; then
      warn "SIGTERM didn't take -- sending SIGKILL"
      kill -9 "$GEN_PID" 2>/dev/null || true
      sleep 1
    fi
    ok "Killed old PID $GEN_PID"
  fi

  # Source env to get MINIMAX_API_KEY if available
  if [[ -f "$HOME/.zo_env" ]]; then
    set +u
    # shellcheck disable=SC1091
    source "$HOME/.zo_env"
    set -u
    ok "Sourced ~/.zo_env (MiniMax key loaded if present)"
  fi

  nohup python3 "$GEN" >> "$LOG" 2>&1 &
  NEW_PID=$!
  sleep 3
  if kill -0 "$NEW_PID" 2>/dev/null; then
    ok "Generator restarted -- new PID $NEW_PID"
  else
    fail "Generator failed to start -- check $LOG"
  fi
fi

# ==============================================================================
# Verification
# ==============================================================================
echo
say "Verification"

if [[ "$DRY_RUN" == "0" ]]; then
  # Gap A: count entries in the Python set
  PY_COUNT="$(python3 -c "
import re
src = open('$GEN').read()
m = re.search(r'ALREADY_BUILT\s*=\s*\{([^}]*)\}', src, re.DOTALL)
if m:
    items = [x.strip().strip('\"') for x in m.group(1).split(',') if x.strip()]
    print(len(items))
else:
    print(0)
")"
  if [[ "$PY_COUNT" -ge "$COUNT" ]]; then
    ok "Gap A: ALREADY_BUILT now has $PY_COUNT entries (was 30; schema has $COUNT)"
  else
    warn "Gap A: ALREADY_BUILT count $PY_COUNT < schema count $COUNT -- inspect manually"
  fi

  # Gap C: refresh call present?
  if grep -q 'refresh_schema_doc' "$GEN"; then
    ok "Gap C: refresh_schema_doc subprocess call present in run_cycle()"
  else
    warn "Gap C: refresh call NOT found -- inspect manually"
  fi

  # Gap B: give it a moment, then check heartbeat
  if [[ "$SKIP_RESTART" == "0" ]]; then
    say "Waiting 8s for first cycle to run and write heartbeat..."
    sleep 8
    HB="$(curl -s -X POST http://127.0.0.1:8772/query \
          -H 'Content-Type: application/json' \
          -d '{"sql":"SELECT service, last_heartbeat FROM service_health WHERE service='"'"'sentinel_directive_generator'"'"'"}' \
          2>/dev/null | grep -o '"service":"sentinel_directive_generator"' || true)"
    if [[ -n "$HB" ]]; then
      ok "Gap B: heartbeat confirmed in service_health"
    else
      warn "Gap B: heartbeat not yet visible -- check again in a minute with:"
      echo "    curl -s -X POST http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\"
      echo "      -d '{\"sql\":\"SELECT service, last_heartbeat FROM service_health WHERE service=\\\\\\'sentinel_directive_generator\\\\\\'\"}'"
    fi
  fi

  echo
  ok "All patches applied. Backup: $BACKUP_DIR"
  echo
  say "Rollback (if needed):"
  echo "    cp $BACKUP_DIR/sentinel_directive_generator.py $GEN"
  [[ "$SKIP_RESTART" == "0" ]] && echo "    pkill -f sentinel_directive_generator.py && nohup python3 $GEN >> $LOG 2>&1 &"
else
  echo
  ok "Dry-run complete. Re-run without DRY_RUN=1 to apply."
fi