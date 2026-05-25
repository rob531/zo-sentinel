#!/usr/bin/env bash
# patch_supply_chain_scale.sh
# ------------------------------------------------------------------------------
# Fix the scale bug in supply_chain_enrichment.py.
# Builder's MiniMax produced scores in [0.0, 1.0]. Harness contract requires
# [0.0, 100.0]. Two-line fix: multiply by 100 at final cap.
# Idempotent, AST-validated, backed up.
# ------------------------------------------------------------------------------
set -uo pipefail

FILE=/home/workspace/zo_sentinel/supply_chain_enrichment.py
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; NC=$'\033[0m'
ok()  { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad() { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }

[[ -f "$FILE" ]] || { bad "$FILE missing"; exit 2; }
python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "source parses cleanly" \
    || { bad "syntax errors present -- aborting"; exit 2; }

if grep -q 'min(100.0, final_score \* 100.0)' "$FILE"; then
    ok "already patched. Skipping."
    exit 0
fi

cp "$FILE" "$FILE.bak.$TS"
ok "backup -> $FILE.bak.$TS"

python3 <<'PYEOF'
path = "/home/workspace/zo_sentinel/supply_chain_enrichment.py"
src = open(path).read()
import ast

old = "    final_score = max(0.0, min(1.0, final_score))"
new = "    final_score = max(0.0, min(100.0, final_score * 100.0))"

if old not in src:
    print("ERROR: expected line not found")
    exit(3)
if src.count(old) != 1:
    print("ERROR: expected exactly 1 occurrence, found", src.count(old))
    exit(3)

src = src.replace(old, new, 1)

# Also update the docstring comment that claims 0.0-1.0 range
old_doc = "        tuple: (score: float 0.0-1.0, evidence: dict)"
new_doc = "        tuple: (score: float 0.0-100.0, evidence: dict)"
src = src.replace(old_doc, new_doc, 1)

try:
    ast.parse(src)
except SyntaxError as e:
    print("ERROR: patched source has syntax error:", e)
    exit(4)

open(path, 'w').write(src)
print("ok: wrote", len(src), "bytes")
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

ok "Done. Scale fixed. Ready for harness."