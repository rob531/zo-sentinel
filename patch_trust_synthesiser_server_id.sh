#!/usr/bin/env bash
# patch_trust_synthesiser_server_id.sh
# ------------------------------------------------------------------------------
# Final fix for trust_synthesiser -- the "tool_name" legacy misnomer.
#
# Context: trust_synthesiser's internal variable tool_name holds what is
# actually a server_id (the pivot SQL does SELECT server_id AS tool_name).
# write_verdict_to_registry() builds a write payload with key 'tool_name' and
# sends to write_service, which generates INSERT ... ON CONFLICT(server_id).
# mcp_server_registry has NO tool_name column -- the PK is server_id.
# Result: DuckDB Binder assertion failure on every verdict write. 500 error.
#
# Fix: rename the payload key from 'tool_name' to 'server_id' in
# write_verdict_to_registry. The variable name inside trust_synthesiser stays
# tool_name (to minimise blast radius) -- only the outbound JSON key changes.
#
# Safety: idempotent, AST-validated, .bak backup, kill+restart.
# ------------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
FILE="$SENTINEL/trust_synthesiser.py"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; NC=$'\033[0m'
h1() { printf "\n%s=== %s ===%s\n" "$CYA" "$*" "$NC"; }
ok() { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad() { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n" "$YLW" "$NC" "$*"; }

h1 "trust_synthesiser tool_name -> server_id payload fix"

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "source parses cleanly" \
    || { bad "syntax errors present -- aborting"; exit 2; }

# Idempotency
if grep -q "'server_id': tool_name," "$FILE"; then
    ok "already patched (payload uses server_id). Skipping."
    exit 0
fi

cp "$FILE" "$FILE.bak.$TS"
ok "backup -> $FILE.bak.$TS"

python3 <<'PYEOF'
path = "/home/workspace/zo_sentinel/trust_synthesiser.py"
src = open(path).read()
import ast

# Replace ONLY the payload dict inside write_verdict_to_registry.
# Targeting the exact 'tool_name': tool_name, line in the data = {...} block.
old = "    data = {\n        'tool_name': tool_name,"
new = "    data = {\n        'server_id': tool_name,"

if old not in src:
    print("ERROR: expected payload block not found")
    exit(3)

# Count occurrences before replacing -- should be exactly 1
if src.count(old) != 1:
    print(f"ERROR: expected exactly 1 occurrence, found {src.count(old)}")
    exit(3)

src = src.replace(old, new, 1)

try:
    ast.parse(src)
except SyntaxError as e:
    print(f"ERROR: patched source has syntax error: {e}")
    exit(4)

open(path, 'w').write(src)
print(f"ok: wrote {len(src)} bytes")
PYEOF

RC=$?
if [[ $RC -ne 0 ]]; then
    bad "rewriter failed -- rolling back"
    cp "$FILE.bak.$TS" "$FILE"
    exit $RC
fi

python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "post-patch: parses cleanly" \
    || {
        bad "post-patch: syntax error -- rolling back"
        cp "$FILE.bak.$TS" "$FILE"
        exit 4
    }

h1 "Restart trust_synthesiser"
if pgrep -f 'python3 .*trust_synthesiser.py' >/dev/null 2>&1; then
    pkill -9 -f 'python3 .*trust_synthesiser.py' 2>/dev/null
    warn "killed old trust_synthesiser"
fi
rm -f /tmp/trust_synthesiser.lock 2>/dev/null
sleep 2
setsid python3 "$FILE" >> "$LOGS/sentinel_trust_synthesiser.log" 2>&1 <&- &
sleep 4
pid="$(pgrep -f 'python3 .*trust_synthesiser.py' 2>/dev/null | head -1)"
if [[ -n "$pid" ]]; then
    ok "trust_synthesiser PID $pid"
else
    bad "failed to start -- last 5 lines:"
    tail -5 "$LOGS/sentinel_trust_synthesiser.log" | sed 's/^/    /'
    exit 4
fi

h1 "Wait 45s for first cycle to complete"
sleep 45

echo
echo "Recent log tail (look for 'Written verdict' success lines):"
tail -10 "$LOGS/sentinel_trust_synthesiser.log" | sed 's/^/    /'

echo
ok "Done. Ask Claude to re-run the verdict distribution query."
echo
echo "Rollback: cp $FILE.bak.$TS $FILE"