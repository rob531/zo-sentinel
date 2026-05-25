#!/usr/bin/env bash
# patch_trust_synthesiser_endpoint.sh
# ------------------------------------------------------------------------------
# REAL root cause of "No MCP signal scores found":
# trust_synthesiser.query_signal_scores() calls write_service /execute
# endpoint for a SELECT query. But /execute is fire-and-forget -- it runs
# the SQL and returns only {"ok": true}, discarding the result set.
#
# /query is the correct endpoint for SELECTs -- it returns {"rows": [...]}.
# signal_analyser was fixed for exactly this reason back in v1.1. The same
# fix needs to be applied here.
#
# Fix: replace EXECUTE_URL with a /query URL in query_signal_scores() only.
# Keep EXECUTE_URL everywhere else (it's fine for table creates etc).
#
# Safety: idempotent, AST-validated, .bak backup, kill+restart trust_synthesiser.
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

h1 "trust_synthesiser /execute -> /query fix"

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "source parses cleanly" \
    || { bad "syntax errors present -- aborting"; exit 2; }

# Idempotency check
if grep -q '^QUERY_URL = ' "$FILE"; then
    ok "already patched (QUERY_URL present). Skipping."
    exit 0
fi

cp "$FILE" "$FILE.bak.$TS"
ok "backup -> $FILE.bak.$TS"

python3 <<'PYEOF'
path = "/home/workspace/zo_sentinel/trust_synthesiser.py"
src = open(path).read()
import ast, re

# Step 1: add QUERY_URL constant next to EXECUTE_URL
if "QUERY_URL = 'http://127.0.0.1:8772/query'" not in src:
    src = src.replace(
        "EXECUTE_URL = 'http://127.0.0.1:8772/execute'",
        "EXECUTE_URL = 'http://127.0.0.1:8772/execute'\nQUERY_URL = 'http://127.0.0.1:8772/query'",
        1
    )

# Step 2: inside query_signal_scores, swap the request target from EXECUTE_URL to QUERY_URL
old_call = (
    "        response = requests.post(\n"
    "            EXECUTE_URL,\n"
    "            json={'sql': sql, 'timeout': 30},\n"
    "            timeout=35\n"
    "        )"
)
new_call = (
    "        response = requests.post(\n"
    "            QUERY_URL,\n"
    "            json={'sql': sql},\n"
    "            timeout=35\n"
    "        )"
)
if old_call not in src:
    print("ERROR: expected EXECUTE_URL call block not found in query_signal_scores")
    exit(3)

src = src.replace(old_call, new_call, 1)

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

h1 "Wait 30s for first cycle"
sleep 30

echo
echo "Recent log tail:"
tail -8 "$LOGS/sentinel_trust_synthesiser.log" | sed 's/^/    /'

echo
ok "Done. Ask Claude to re-run the verdict distribution query."
echo
echo "Rollback: cp $FILE.bak.$TS $FILE"