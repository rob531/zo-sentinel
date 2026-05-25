#!/usr/bin/env bash
# patch_trust_synthesiser_pivot.sh
# ------------------------------------------------------------------------------
# STAGED -- DRY_RUN IS THE DEFAULT. Pass APPLY=1 to actually run.
# Drafted: 2026-04-17 01:45 UTC for morning review.
#
# Fixes the root cause of the "every MCP has trust_score=74.0" UI bug.
#
# Problem:
#   trust_synthesiser.query_signal_scores() runs:
#     SELECT tool_name, domain_trust, tool_description_safety, ...
#     FROM mcp_signal_scores
#   This has been broken since Phase 3 shipped. mcp_signal_scores is
#   LONG-FORMAT: columns are (id, server_id, signal_name, score, evidence,
#   scored_at). The query asks for WIDE-FORMAT columns that don't exist.
#
#   Every SELECT returns a 500 error. trust_synthesiser's outer try/except
#   logs the failure and returns [], which makes every server look like
#   "all signals missing". The fallback path produces a hardcoded
#   trust_score=74.0, verdict=TRUSTED_RESEARCH for every MCP. This is why
#   the 115 assessed MCPs in the UI all have identical cards.
#
# Fix:
#   Pivot the long-format table into wide-format in the SELECT itself using
#   MAX(CASE WHEN signal_name='x' THEN score END) AS x. Keep everything
#   downstream unchanged so the blast radius is minimal.
#
# Why this is safe:
#   - Only SELECT shape changes; write_verdict_to_registry() is untouched.
#   - DuckDB GROUP BY with MAX(CASE WHEN ...) is standard SQL, no extensions.
#   - trust_synthesiser already handles NULL per-signal values (the 'missing'
#     logic). A server with zero signal rows will produce all NULLs, which is
#     exactly what the existing code expects for 'INSUFFICIENT'.
#   - Does NOT touch mcp_signal_scores or any other table.
#
# Pre-conditions before you run this in the morning:
#   1. signal_analyser must be WRITING to mcp_signal_scores. Check with:
#      SELECT COUNT(*), COUNT(DISTINCT server_id) FROM mcp_signal_scores
#      If zero rows, this patch does nothing useful -- signal_analyser is
#      still broken somehow and needs separate investigation.
#   2. write_service :8772 healthy
#   3. trust_synthesiser.py currently parses cleanly
#
# Usage:
#   bash patch_trust_synthesiser_pivot.sh            # dry-run (default, SAFE)
#   APPLY=1 bash patch_trust_synthesiser_pivot.sh    # actually apply
#   APPLY=1 SKIP_RESTART=1 bash patch_trust_synthesiser_pivot.sh
# ------------------------------------------------------------------------------

set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
FILE="$SENTINEL/trust_synthesiser.py"
APPLY="${APPLY:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; DIM=$'\033[2m'; BLD=$'\033[1m'; NC=$'\033[0m'
h1()   { printf "\n%s%s=== %s ===%s\n" "$BLD" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
stage(){ printf "  %s[STAGE]%s %s\n" "$DIM" "$NC" "$*"; }

h1 "trust_synthesiser long-format pivot patcher"
printf "timestamp: %s  apply: %s\n" "$TS" "$APPLY"
if [[ "$APPLY" != "1" ]]; then
    warn "Running in DRY-RUN mode. No file changes will be made."
    warn "To apply: APPLY=1 bash $0"
fi

# ------------------------------------------------------------------------------
# Pre-flight
# ------------------------------------------------------------------------------
h1 "Pre-flight"

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
ok "Source file present"

python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "Current source parses cleanly" \
    || { bad "Current source has syntax errors -- aborting"; exit 2; }

WS="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null || echo 000)"
[[ "$WS" == "200" ]] && ok "write_service :8772 healthy" || { bad "write_service :8772 returned $WS -- aborting"; exit 2; }

# Data pre-condition: is signal_analyser actually writing?
SIG_N="$(curl -s -X POST http://127.0.0.1:8772/query \
    -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT COUNT(*) AS n FROM mcp_signal_scores"}' 2>/dev/null \
    | grep -oE '"n":[0-9]+' | head -1 | grep -oE '[0-9]+')"
SIG_N="${SIG_N:-0}"
if [[ "$SIG_N" -lt 10 ]]; then
    bad "mcp_signal_scores has only $SIG_N rows -- signal_analyser not producing enough data yet."
    bad "Patching trust_synthesiser now would produce verdicts for almost no servers."
    bad "Wait ~30 min after patch_missing_pk_constraints completed, then retry."
    exit 3
fi
ok "mcp_signal_scores has $SIG_N rows -- trust_synthesiser will have data to work with"

# ------------------------------------------------------------------------------
# What will change -- show the SQL being replaced so reviewer can verify
# ------------------------------------------------------------------------------
h1 "Change preview"
stage "In $FILE, replace query_signal_scores() SQL from WIDE-format to LONG-format pivot."
stage ""
stage "BEFORE (broken; columns don't exist in live table):"
cat <<'EOF'
    sql = """
    SELECT 
        tool_name,
        domain_trust,
        tool_description_safety,
        permission_scope,
        supply_chain,
        community_signal,
        temporal_stability,
        last_updated
    FROM mcp_signal_scores
    """
EOF
stage ""
stage "AFTER (pivot using MAX(CASE WHEN ...) per signal_name):"
cat <<'EOF'
    sql = """
    SELECT
        server_id AS tool_name,
        MAX(CASE WHEN signal_name='domain_trust'            THEN score END) AS domain_trust,
        MAX(CASE WHEN signal_name='tool_description_safety' THEN score END) AS tool_description_safety,
        MAX(CASE WHEN signal_name='permission_scope'        THEN score END) AS permission_scope,
        MAX(CASE WHEN signal_name='supply_chain'            THEN score END) AS supply_chain,
        MAX(CASE WHEN signal_name='community_signal'        THEN score END) AS community_signal,
        MAX(CASE WHEN signal_name='temporal_stability'      THEN score END) AS temporal_stability,
        MAX(scored_at)                                                       AS last_updated
    FROM mcp_signal_scores
    GROUP BY server_id
    """
EOF
stage ""
stage "No other code in trust_synthesiser.py changes. The record.get('tool_name')"
stage "and record.get('domain_trust') calls downstream all continue to work because"
stage "the output row-shape is identical."
stage ""
stage "write_verdict_to_registry() still writes 'tool_name' as the key. IMPORTANT"
stage "context: mcp_server_registry uses 'server_id' as its PK, and the pivot aliases"
stage "server_id AS tool_name. So trust_synthesiser is really writing BY server_id"
stage "but labeling it tool_name. That's consistent with current behaviour -- the"
stage "field name is just a legacy misnomer inside trust_synthesiser."

if [[ "$APPLY" != "1" ]]; then
    h1 "Dry-run complete"
    echo
    echo "Review checklist before running APPLY=1:"
    echo "  [ ] mcp_signal_scores has enough rows to score against ($SIG_N currently)"
    echo "  [ ] Signal names in the table MATCH the CASE WHEN predicates:"
    echo "      SELECT DISTINCT signal_name FROM mcp_signal_scores"
    echo "      Expected: domain_trust, tool_description_safety, permission_scope,"
    echo "                supply_chain, community_signal, temporal_stability"
    echo "  [ ] trust_synthesiser.py is not currently mid-cycle (pkill if needed first)"
    echo "  [ ] You have 5 minutes of attention to verify the first cycle's output"
    echo
    echo "When ready:  APPLY=1 bash $0"
    exit 0
fi

# ------------------------------------------------------------------------------
# APPLY mode
# ------------------------------------------------------------------------------
h1 "Apply"

cp "$FILE" "$FILE.bak.$TS"
ok "Backup -> $FILE.bak.$TS"

REWRITER="$(mktemp)"
cat > "$REWRITER" <<'PYEOF'
import ast, re, sys

path = sys.argv[1]
src = open(path).read()

# Locate the existing query_signal_scores function's SQL.
# Match the whole triple-quoted string, conservatively.
old_pattern = re.compile(
    r'(    sql = """)\s*\n'
    r'    SELECT\s+\n'
    r'        tool_name,\s*\n'
    r'        domain_trust,\s*\n'
    r'        tool_description_safety,\s*\n'
    r'        permission_scope,\s*\n'
    r'        supply_chain,\s*\n'
    r'        community_signal,\s*\n'
    r'        temporal_stability,\s*\n'
    r'        last_updated\s*\n'
    r'    FROM mcp_signal_scores\s*\n'
    r'(    """)',
    re.MULTILINE
)

replacement = (
    '    sql = """\n'
    '    SELECT\n'
    "        server_id AS tool_name,\n"
    "        MAX(CASE WHEN signal_name='domain_trust'            THEN score END) AS domain_trust,\n"
    "        MAX(CASE WHEN signal_name='tool_description_safety' THEN score END) AS tool_description_safety,\n"
    "        MAX(CASE WHEN signal_name='permission_scope'        THEN score END) AS permission_scope,\n"
    "        MAX(CASE WHEN signal_name='supply_chain'            THEN score END) AS supply_chain,\n"
    "        MAX(CASE WHEN signal_name='community_signal'        THEN score END) AS community_signal,\n"
    "        MAX(CASE WHEN signal_name='temporal_stability'      THEN score END) AS temporal_stability,\n"
    "        MAX(scored_at)                                                       AS last_updated\n"
    '    FROM mcp_signal_scores\n'
    '    GROUP BY server_id\n'
    '    """'
)

if not old_pattern.search(src):
    print('ERROR: could not locate the wide-format SELECT block. Aborting.', file=sys.stderr)
    print('Either the source has already been patched, or its shape has drifted.', file=sys.stderr)
    sys.exit(5)

new_src = old_pattern.sub(replacement, src, count=1)

# Sanity: exactly one substitution
if new_src.count('GROUP BY server_id') != 1:
    print('ERROR: expected exactly 1 GROUP BY server_id after replacement. Aborting.', file=sys.stderr)
    sys.exit(5)

# AST validation
try:
    ast.parse(new_src)
except SyntaxError as e:
    print(f'ERROR: patched source has syntax error: {e}', file=sys.stderr)
    sys.exit(5)

open(path, 'w').write(new_src)
print(f'ok: wrote {len(new_src)} bytes')
PYEOF

if python3 "$REWRITER" "$FILE"; then
    python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
        && ok "Post-patch: parses cleanly" \
        || {
            bad "Post-patch: syntax error -- rolling back"
            cp "$FILE.bak.$TS" "$FILE"
            rm -f "$REWRITER"
            exit 4
        }
    ok "Patched $FILE"
else
    bad "Rewriter failed -- rolling back"
    cp "$FILE.bak.$TS" "$FILE" 2>/dev/null
    rm -f "$REWRITER"
    exit 4
fi
rm -f "$REWRITER"

# ------------------------------------------------------------------------------
# Restart
# ------------------------------------------------------------------------------
if [[ "$SKIP_RESTART" == "1" ]]; then
    warn "Skipping restart (SKIP_RESTART=1). Manually restart:"
    echo "    pkill -9 -f trust_synthesiser.py"
    echo "    rm -f /tmp/trust_synthesiser.lock"
    echo "    setsid python3 $FILE >> $LOGS/sentinel_trust_synthesiser.log 2>&1 <&- &"
    exit 0
fi

h1 "Restart trust_synthesiser"
if pgrep -f 'python3 .*trust_synthesiser.py' >/dev/null 2>&1; then
    pkill -9 -f 'python3 .*trust_synthesiser.py' 2>/dev/null
    warn "killed old trust_synthesiser"
fi
rm -f /tmp/trust_synthesiser.lock 2>/dev/null
sleep 2

setsid python3 "$FILE" >> "$LOGS/sentinel_trust_synthesiser.log" 2>&1 <&- &
sleep 3
pid="$(pgrep -f 'python3 .*trust_synthesiser.py' 2>/dev/null | head -1)"
if [[ -n "$pid" ]]; then
    ok "trust_synthesiser PID $pid"
else
    bad "Failed to start -- last 10 lines:"
    tail -10 "$LOGS/sentinel_trust_synthesiser.log" | sed 's/^/    /'
    exit 4
fi

# ------------------------------------------------------------------------------
# Verify
# ------------------------------------------------------------------------------
h1 "Wait for first cycle (may take up to 60s)"
sleep 60

echo
echo "Recent log tail:"
tail -15 "$LOGS/sentinel_trust_synthesiser.log" | sed 's/^/    /'

echo
h1 "Verdict distribution after first cycle"
curl -s -X POST http://127.0.0.1:8772/query \
    -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT verdict, ROUND(AVG(trust_score),1) AS avg_score, COUNT(*) AS n FROM mcp_server_registry WHERE verdict IS NOT NULL GROUP BY verdict ORDER BY n DESC"}' 2>/dev/null \
    | python3 -c 'import sys,json
try:
    d = json.loads(sys.stdin.read())
    print()
    print(f"  {\"verdict\":28s} {\"avg_score\":>12s} {\"count\":>6s}")
    print("  " + "-"*48)
    for r in d.get("rows", []):
        print(f"  {r[\"verdict\"]:28s} {r[\"avg_score\"]:>12} {r[\"n\"]:>6}")
except Exception as e:
    print(f"  parse error: {e}")'

echo
warn "If you still see only TRUSTED_RESEARCH 74.0 across the board, check:"
echo "  1. grep -i 'no mcp signal scores' $LOGS/sentinel_trust_synthesiser.log"
echo "     -- means the pivot returned empty results (signal_analyser not writing)"
echo "  2. grep -i error $LOGS/sentinel_trust_synthesiser.log"
echo "     -- means the pivot SQL itself is failing"
echo
ok "Done. Rollback if needed:"
echo "  cp $FILE.bak.$TS $FILE"
echo "  pkill -9 -f trust_synthesiser.py"
echo "  rm -f /tmp/trust_synthesiser.lock"