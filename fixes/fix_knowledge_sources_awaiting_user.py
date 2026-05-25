#!/usr/bin/env python3
"""
fix_knowledge_sources_awaiting_user.py

Two surgical edits to directive_knowledge_sources.py:

1. Add AWAITING_USER_TABLES constant listing tables that are legitimately
   empty until user/admin action populates them.

2. Split the gaps_map's 'empty tables' section into two subsections:
   - 'Core tables empty but awaiting user action (NORMAL)'
   - 'Core tables empty and indicating pipeline gap (INVESTIGATE)'

   This stops the directive generator from misreading user-awaiting
   tables as pipeline failures.

Idempotent. Reads the file, applies anchor-based replacements, AST-validates,
backs up, writes.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/directive_knowledge_sources.py")

# ---- Patch A: constant ------------------------------------------------------

CONST_ANCHOR = """CORE_TABLES = [
    \"mcp_server_registry\", \"mcp_signal_scores\", \"mcp_signal_enrichments\",
    \"mcp_threat_associations\", \"mcp_risk_register\", \"mcp_attestations\",
    \"mcp_definition_history\", \"mcp_submissions\", \"mcp_exemptions\",
    \"mcp_decisions\", \"mcp_policy_rules\", \"mcp_fingerprints\",
    \"mcp_tool_hashes\",
]"""

CONST_NEW = CONST_ANCHOR + """

# Tables that are legitimately empty until user / admin action populates them.
# Empty rows in these tables is NOT a pipeline failure — it's a new-install
# state. The directive generator must be told this explicitly or it will
# propose \"fixes\" for working infrastructure.
AWAITING_USER_TABLES = {
    \"mcp_submissions\",   # empty until a user submits an MCP via the portal
    \"mcp_exemptions\",    # empty until an admin grants an exemption
    \"mcp_decisions\",     # empty until approval_workflow runs
    \"mcp_policy_rules\",  # empty until an admin authors a policy
    \"mcp_fingerprints\",  # populates after mcp_fingerprinter cycles
    \"mcp_tool_hashes\",   # populates after mcp_scanner cycles
}"""

# ---- Patch B: gaps_map empty-tables section --------------------------------

GAPS_OLD = """    # Empty core tables — pipeline starved somewhere
    empties = [t for t in _table_counts() if t[\"n\"] == 0]
    if empties:
        parts.append(\"### Core tables with zero rows (pipeline gap or new-install)\")
        for t in empties:
            parts.append(f\"  - {t['table']}\")
        parts.append(\"\")"""

GAPS_NEW = """    # Empty core tables — split into two classes so generator reads them correctly
    empties = [t for t in _table_counts() if t[\"n\"] == 0]
    awaiting   = [t for t in empties if t[\"table\"] in AWAITING_USER_TABLES]
    pipe_gaps  = [t for t in empties if t[\"table\"] not in AWAITING_USER_TABLES]
    if awaiting:
        parts.append(\"### Empty tables awaiting user/admin action (NORMAL — do NOT propose fixes)\")
        for t in awaiting:
            parts.append(f\"  - {t['table']}\")
        parts.append(\"\")
    if pipe_gaps:
        parts.append(\"### Empty tables indicating pipeline gap (INVESTIGATE)\")
        for t in pipe_gaps:
            parts.append(f\"  - {t['table']}\")
        parts.append(\"\")"""


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("Knowledge sources: awaiting-user-tables patch")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}"); return 2
    src = TARGET.read_text()
    changed = False

    # Patch A
    if "AWAITING_USER_TABLES" in src:
        print("  [skip A] AWAITING_USER_TABLES already present")
    elif CONST_ANCHOR in src:
        src = src.replace(CONST_ANCHOR, CONST_NEW, 1)
        print("  [patch A] added AWAITING_USER_TABLES constant")
        changed = True
    else:
        print("  [FAIL A] CORE_TABLES anchor not found verbatim")
        return 2

    # Patch B
    if "awaiting user/admin action (NORMAL" in src:
        print("  [skip B] gaps_map already splits awaiting vs pipe-gap")
    elif GAPS_OLD in src:
        src = src.replace(GAPS_OLD, GAPS_NEW, 1)
        print("  [patch B] split empty-tables section into two labeled subsections")
        changed = True
    else:
        print("  [FAIL B] empty-tables anchor not found verbatim")
        return 2

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify with:")
    print("  python3 /home/workspace/zo_sentinel/directive_knowledge_sources.py 2>&1 | tail -40")
    return 0


if __name__ == "__main__":
    sys.exit(main())