#!/usr/bin/env bash
# patch_sentinel_port_8773_to_8772.sh
# ------------------------------------------------------------------------------
# Narrow fix: replace EXECUTE_URL port :8773 with :8772 in three sentinel daemons.
# :8773 was an old inference_router address; write_service now handles both
# /execute and /query on :8772.
#
# Gate 2 identified this on 2026-04-17 across:
#     threat_intel_ingestor.py
#     attestation_engine.py
#     rug_pull_monitor.py
#
# All three share the identical line:
#     EXECUTE_URL = 'http://127.0.0.1:8773/execute'
# which we rewrite to:
#     EXECUTE_URL = 'http://127.0.0.1:8772/execute'
#
# Safety:
#   - Exact string match with occurrence-count guard (refuses to patch if !=1)
#   - Per-file .bak backup (timestamped)
#   - AST validation on each patched file; rollback on parse failure
#   - Idempotent (detects already-patched state and skips)
#
# NOT fixed by this patcher (deliberately separate tasks):
#   - ws_query routing SELECTs to EXECUTE_URL (endpoint_semantic_mismatch) -- C.2/C.3
#   - Double-slash URL bug in ws_write() (f'{WRITE_SERVICE_URL}/write')    -- C.2/C.3
#   - legacy_pidfile_pattern (pidfile under /var/run/zo/)                   -- C.2/C.3
#   - race_prone_id_gen (SELECT MAX(id)+1) in rug_pull_monitor              -- C.3
#   - missing source= field in threat_intel_ingestor payloads              -- C.2
# ------------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; NC=$'\033[0m'
h1()   { printf "\n%s=== %s ===%s\n" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n" "$YLW" "$NC" "$*"; }

FILES=(
    "threat_intel_ingestor.py"
    "attestation_engine.py"
    "rug_pull_monitor.py"
)

OLD="EXECUTE_URL = 'http://127.0.0.1:8773/execute'"
NEW="EXECUTE_URL = 'http://127.0.0.1:8772/execute'"

h1 "port :8773 -> :8772 in sentinel daemons"

TOTAL_PATCHED=0
TOTAL_SKIPPED=0
TOTAL_FAILED=0

for name in "${FILES[@]}"; do
    path="$SENTINEL/$name"
    echo
    echo "--- $name ---"

    if [[ ! -f "$path" ]]; then
        bad "not found, skipping"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi

    # Pre-patch AST check
    if ! python3 -c "import ast; ast.parse(open('$path').read())" 2>/dev/null; then
        bad "pre-patch AST invalid -- skipping"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi

    # Idempotency
    if grep -qF "$NEW" "$path" && ! grep -qF "$OLD" "$path"; then
        ok "already patched"
        TOTAL_SKIPPED=$((TOTAL_SKIPPED + 1))
        continue
    fi

    # Exact occurrence count (must be 1)
    count=$(grep -cF "$OLD" "$path" || true)
    if [[ "$count" -eq 0 ]]; then
        warn "expected OLD string not found -- file may use a variant"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi
    if [[ "$count" -ne 1 ]]; then
        bad "OLD string found $count times (expected 1) -- refusing to patch"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi

    # Backup
    cp "$path" "$path.bak.$TS"
    ok "backup -> $path.bak.$TS"

    # Patch (python heredoc for safe string replacement, avoids sed shell-quoting issues)
    python3 <<PYEOF
import ast
path = "$path"
src = open(path).read()
old = "$OLD"
new = "$NEW"
if src.count(old) != 1:
    print("ERROR: occurrence count changed mid-flight")
    exit(3)
src = src.replace(old, new, 1)
try:
    ast.parse(src)
except SyntaxError as e:
    print(f"ERROR: post-patch AST invalid: {e}")
    exit(4)
open(path, 'w').write(src)
print(f"wrote {len(src)} bytes")
PYEOF

    RC=$?
    if [[ $RC -ne 0 ]]; then
        bad "patch failed (rc=$RC) -- rolling back"
        cp "$path.bak.$TS" "$path"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi

    # Post-patch verification
    if ! python3 -c "import ast; ast.parse(open('$path').read())" 2>/dev/null; then
        bad "post-patch AST invalid -- rolling back"
        cp "$path.bak.$TS" "$path"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
        continue
    fi

    if grep -qF "$NEW" "$path" && ! grep -qF "$OLD" "$path"; then
        ok "patched cleanly, AST valid"
        TOTAL_PATCHED=$((TOTAL_PATCHED + 1))
    else
        bad "verification grep failed -- rolling back"
        cp "$path.bak.$TS" "$path"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi
done

h1 "Summary"
echo "  patched: $TOTAL_PATCHED"
echo "  skipped: $TOTAL_SKIPPED (already correct)"
echo "  failed:  $TOTAL_FAILED"

if [[ $TOTAL_FAILED -gt 0 ]]; then
    warn "Some files did not patch cleanly. See messages above."
    exit 1
fi

h1 "Rerun Gate 2 to confirm port_mismatch is resolved"
echo
echo "  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 2 \\"
echo "      > /home/workspace/logs/gate_results.txt 2>&1"
echo
echo "Then ask Claude to read gate_results.txt -- expect port_mismatch"
echo "occurrences count to stay the same (dedup) but is_novel flips to false,"
echo "and the 3 previously-failing checks flip to [OK]."
echo

ok "Done"