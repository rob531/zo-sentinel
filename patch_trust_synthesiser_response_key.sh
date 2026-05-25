#!/usr/bin/env bash
# patch_trust_synthesiser_response_key.sh
# ------------------------------------------------------------------------------
# Fixes one final bug in trust_synthesiser.query_signal_scores():
#
# The pivot SQL is correct. The write_service response is being parsed wrong.
# write_service returns {"rows": [...]} but trust_synthesiser looks for
# {"results": [...]} -- so result['results'] is missing, the function falls
# through to 'return []', and the daemon logs "No MCP signal scores found"
# even though the SQL itself ran fine.
#
# This is a latent bug that was masked by the previous wide-format 500 error.
# Now that the pivot makes the SQL succeed, this response-parsing bug surfaced.
#
# Safety: AST-validated edit, backed up to .bak.<ts>, idempotent.
# ------------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
FILE="$SENTINEL/trust_synthesiser.py"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; NC=$'\033[0m'
h1()   { printf "\n%s=== %s ===%s\n" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }

h1 "trust_synthesiser response-key fix"

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "source parses cleanly" \
    || { bad "syntax errors present -- aborting"; exit 2; }

# Idempotency: if we've already done this, skip.
if grep -q "if 'rows' in result" "$FILE"; then
    ok "already patched (looking for 'rows' key). Skipping."
    exit 0
fi

if ! grep -q "if 'results' in result and result\['results'\]" "$FILE"; then
    warn "expected pattern not found. File may have drifted from expected shape."
    warn "No changes applied. Please review manually."
    exit 3
fi

cp "$FILE" "$FILE.bak.$TS"
ok "backup -> $FILE.bak.$TS"

# Replace the two offending lines.
python3 <<PYEOF
path = "$FILE"
src = open(path).read()

# Replace the response-parsing pair of lines.
old = """        if 'results' in result and result['results']:
            return result['results']"""
new = """        if 'rows' in result and result['rows']:
            return result['rows']
        if 'results' in result and result['results']:
            return result['results']"""

if old not in src:
    print("ERROR: expected block not found")
    exit(3)

src = src.replace(old, new, 1)

import ast
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
ok "done. Ask Claude to check verdict distribution for real results."
echo
echo "Rollback: cp $FILE.bak.$TS $FILE"