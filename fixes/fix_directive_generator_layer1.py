#!/usr/bin/env python3
"""
fix_directive_generator_layer1.py

Layer 1 prompt enrichment patch. Adds three live knowledge sources to the
directive generator's prompt:
  1. PRODUCT_SPEC.md content (static target)
  2. live_wiring_map()      (live system snapshot)
  3. live_gaps_map()        (spec vs reality diff)

Changes two things in sentinel_directive_generator.py:
  A. Adds `import directive_knowledge_sources as dks` near the top
  B. Modifies build_prompt() to fetch dks.assemble_layer1_context() and
     splice its three sections into the prompt between 'Current Build State'
     and 'ZO-SENTINEL Knowledge Base'.

Idempotent: detects the presence of the import and the marker comment to
skip if already applied.

No regex on source. Plain string anchoring only. AST-validated.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/sentinel_directive_generator.py")

# ---- Patch A: import statement ---------------------------------------------

IMPORT_OLD = "import os, json, time, logging, requests, hashlib\n"
IMPORT_NEW = (
    "import os, json, time, logging, requests, hashlib\n"
    "import directive_knowledge_sources as dks  # Layer 1 (2026-04-18)\n"
)

# ---- Patch B: build_prompt() body -----------------------------------------
# We find the unique prefix of the current prompt string and inject our three
# sections before the existing 'ZO-SENTINEL Knowledge Base' header.

PROMPT_OLD_MARKER = """A signal is only useful if it discriminates between servers. When every
server gets the same score, that signal contributes nothing to the verdict.
The heuristic is: BAD SIGNAL = SAME SIGNAL across all inputs.

## ZO-SENTINEL Knowledge Base
{schema}"""

PROMPT_NEW_MARKER = """A signal is only useful if it discriminates between servers. When every
server gets the same score, that signal contributes nothing to the verdict.
The heuristic is: BAD SIGNAL = SAME SIGNAL across all inputs.

{product_spec}

{wiring_map}

{gaps_map}

## How to use the three sections above

- PRODUCT_SPEC.md is the target v1.0 definition. Propose directives that
  close gaps IT lists. Do NOT propose items explicitly excluded from v1.0.
- The wiring map is a live snapshot of what exists and what heartbeats.
  Files in 'Recently built' are REAL — don't propose to rebuild them.
- The gaps map identifies concrete named files / daemons / tables that
  the spec asks for but the live system does not yet have. THESE ARE
  YOUR PRIMARY DIRECTIVE CANDIDATES.

## ZO-SENTINEL Knowledge Base
{schema}"""

# Also need to update build_prompt() to accept and use the new format keys.
# Find the current f-string format call and expand it. The current
# build_prompt() uses an f-string throughout; we just need the run_cycle()
# to provide the new context variables.

BUILD_PROMPT_CALL_OLD = "    prompt = build_prompt(schema, failed, failures_detail, reg_summary, queue)"
BUILD_PROMPT_CALL_NEW = """    # Layer 1: pull live knowledge sources (spec, wiring, gaps)
    try:
        layer1 = dks.assemble_layer1_context()
    except Exception as e:
        log.warning("Layer 1 context assembly failed: %s", e)
        layer1 = {"product_spec": "[Layer 1 unavailable]",
                  "wiring_map": "[Layer 1 unavailable]",
                  "gaps_map": "[Layer 1 unavailable]"}
    prompt = build_prompt(schema, failed, failures_detail, reg_summary, queue,
                          layer1=layer1)"""

# Update build_prompt signature + body to accept and use layer1.
BUILD_PROMPT_SIG_OLD = """def build_prompt(schema: str, failed: list, failures_detail: str,
                registry_summary: str, queue_depth: int) -> str:"""
BUILD_PROMPT_SIG_NEW = """def build_prompt(schema: str, failed: list, failures_detail: str,
                registry_summary: str, queue_depth: int,
                layer1: dict | None = None) -> str:"""

# In the f-string return, we need to expose the Layer 1 context as format
# keys the template already uses (product_spec, wiring_map, gaps_map).
# Simplest approach: add three local variables before the return f""" ... """.
RETURN_HEADER_OLD = "    protected  = _nl.join(f\"  - {f}\" for f in sorted(PROTECTED_FILES))"
RETURN_HEADER_NEW = (
    "    protected  = _nl.join(f\"  - {f}\" for f in sorted(PROTECTED_FILES))\n"
    "    # Layer 1 knowledge sources (defensive defaults if missing)\n"
    "    layer1       = layer1 or {}\n"
    "    product_spec = layer1.get('product_spec', '[PRODUCT_SPEC unavailable]')\n"
    "    wiring_map   = layer1.get('wiring_map',   '[wiring_map unavailable]')\n"
    "    gaps_map     = layer1.get('gaps_map',     '[gaps_map unavailable]')"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("Layer 1 prompt enrichment patcher")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}"); return 2
    src = TARGET.read_text()
    changed = False

    # Patch A: import
    if "import directive_knowledge_sources" in src:
        print("  [skip A] import already present")
    elif IMPORT_OLD in src:
        src = src.replace(IMPORT_OLD, IMPORT_NEW, 1)
        print("  [patch A] added dks import")
        changed = True
    else:
        print("  [FAIL A] expected import line not found verbatim")
        return 2

    # Patch B1: signature
    if "layer1: dict | None = None" in src:
        print("  [skip B1] signature already includes layer1")
    elif BUILD_PROMPT_SIG_OLD in src:
        src = src.replace(BUILD_PROMPT_SIG_OLD, BUILD_PROMPT_SIG_NEW, 1)
        print("  [patch B1] build_prompt signature updated")
        changed = True
    else:
        print("  [FAIL B1] build_prompt signature did not match expected form")
        return 2

    # Patch B2: local variables in build_prompt body
    if "product_spec = layer1.get(" in src:
        print("  [skip B2] local vars already injected")
    elif RETURN_HEADER_OLD in src:
        src = src.replace(RETURN_HEADER_OLD, RETURN_HEADER_NEW, 1)
        print("  [patch B2] injected product_spec / wiring_map / gaps_map locals")
        changed = True
    else:
        print("  [FAIL B2] protected-files setup line did not match expected form")
        return 2

    # Patch B3: prompt template
    if "{wiring_map}" in src and "{gaps_map}" in src:
        print("  [skip B3] prompt template already has layer1 slots")
    elif PROMPT_OLD_MARKER in src:
        src = src.replace(PROMPT_OLD_MARKER, PROMPT_NEW_MARKER, 1)
        print("  [patch B3] prompt template extended with layer1 sections")
        changed = True
    else:
        print("  [FAIL B3] prompt template anchor not found")
        return 2

    # Patch B4: call site in run_cycle
    if "layer1=layer1" in src:
        print("  [skip B4] call site already passes layer1")
    elif BUILD_PROMPT_CALL_OLD in src:
        src = src.replace(BUILD_PROMPT_CALL_OLD, BUILD_PROMPT_CALL_NEW, 1)
        print("  [patch B4] run_cycle now assembles & passes layer1")
        changed = True
    else:
        print("  [FAIL B4] build_prompt() call site did not match expected form")
        return 2

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    # AST-validate before write
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nRestart directive generator to pick up changes:")
    print("  pkill -9 -f 'sentinel_directive_generator.py' 2>/dev/null")
    print("  sleep 2")
    print("  nohup python3 /home/workspace/zo_sentinel/sentinel_directive_generator.py \\")
    print("    >> /home/workspace/logs/sentinel_sentinel_directive_generator.log 2>&1 &")
    print("\nOr if managed by supervisord:")
    print("  supervisorctl -c /etc/zo/supervisord-user.conf restart sentinel_directive_generator")
    print("\nWatch next cycle (runs every 2h, or immediate on restart):")
    print("  tail -f /home/workspace/logs/sentinel_sentinel_directive_generator.log")
    print("\nStandalone self-test of the knowledge sources (useful to inspect):")
    print("  python3 /home/workspace/zo_sentinel/directive_knowledge_sources.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())