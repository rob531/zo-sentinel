#!/usr/bin/env python3
"""
patch_directive_knowledge_sources_add_quality_map.py

Add live_quality_map() to directive_knowledge_sources.py so the directive
generator prompt includes breaker state, quarantined files, and retry
budgets. The generator uses this to decide:
  - Don't propose rebuilding files that are quarantined
  - Don't propose rebuilding files whose retry budget is exhausted
  - If breaker is tripped, don't propose rebuilds AT ALL; only new work

One import, one new function, one new key in assemble_layer1_context.

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/directive_knowledge_sources.py")

# Patch A: add import at module top alongside existing imports. We anchor
# on the first 'import' line we can find reliably. To avoid import-order
# surprises we append to the existing import block.
#
# But directive_knowledge_sources.py imports are not fully visible to us
# -- only the tail of the file. Rather than guess, we add the import
# lazily INSIDE live_quality_map(). That way if gate_quality_state ever
# goes missing, the other sections still assemble.

# Patch B: inject the new function just before assemble_layer1_context.

B_OLD = (
    "# \u2500\u2500 Combined entry point for generator to call "
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\n"
    "def assemble_layer1_context() -> dict[str, str]:\n"
    "    \"\"\"Returns a dict of prompt-ready sections. Generator inserts by key.\"\"\"\n"
    "    return {\n"
    "        \"product_spec\":  load_product_spec(),\n"
    "        \"wiring_map\":    live_wiring_map(),\n"
    "        \"gaps_map\":      live_gaps_map(),\n"
    "    }"
)
B_NEW = (
    "# \u2500\u2500 Quality map \u2500\u2500 breaker + quarantine state (commit 2) "
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\n"
    "def live_quality_map() -> str:\n"
    "    \"\"\"Pull Gate 8 breaker state, quarantined files, and retry budgets.\n"
    "    Generator uses this to avoid re-proposing known-bad rebuilds.\n"
    "\n"
    "    Degrades gracefully: if gate_quality_state is unavailable (module\n"
    "    missing, state file corrupted, etc.), returns a placeholder section\n"
    "    that tells the generator 'quality data unavailable, proceed normally'.\n"
    "    \"\"\"\n"
    "    try:\n"
    "        import sys as _sys\n"
    "        if '/home/workspace/zo_sentinel' not in _sys.path:\n"
    "            _sys.path.insert(0, '/home/workspace/zo_sentinel')\n"
    "        import gate_quality_state as gqs\n"
    "        snap = gqs.snapshot()\n"
    "    except Exception as e:\n"
    "        return (\n"
    "            '## Quality map (Gate 8 breaker + quarantine)\\n'\n"
    "            f'- state unavailable: {e}\\n'\n"
    "            '- generator should proceed normally; do not treat this as a signal\\n'\n"
    "        )\n"
    "\n"
    "    parts = ['## Quality map (Gate 8 circuit breaker + quarantine)']\n"
    "    state = snap.get('state', 'closed')\n"
    "    parts.append(f'- breaker_state: **{state}**')\n"
    "    if snap.get('state_changed_reason'):\n"
    "        parts.append(f'  (since {snap.get(\"state_changed_at\")}: '\n"
    "                     f'{snap.get(\"state_changed_reason\")})')\n"
    "    parts.append('')\n"
    "\n"
    "    # Breaker directive for generator\n"
    "    if state == 'tripped':\n"
    "        parts.append('### !! BREAKER TRIPPED !!')\n"
    "        parts.append('Too many recent builds failed gates. Generator MUST NOT')\n"
    "        parts.append('propose rebuilds of any file listed under retry_budget or')\n"
    "        parts.append('quarantine below. New/unrelated directives are still OK.')\n"
    "        parts.append('Human must run reset_breaker.py to re-enable rebuilds.')\n"
    "        parts.append('')\n"
    "    elif state == 'half-open':\n"
    "        parts.append('### breaker half-open')\n"
    "        parts.append('Rebuilds are permitted for ONE batch. Prefer conservative')\n"
    "        parts.append('changes and explicit spec references in rebuild directives.')\n"
    "        parts.append('')\n"
    "\n"
    "    # Quarantined files\n"
    "    q = snap.get('quarantined', {})\n"
    "    if q:\n"
    "        parts.append('### Quarantined files (DO NOT propose rebuilds)')\n"
    "        for fn, meta in list(q.items())[:20]:\n"
    "            reason = (meta.get('reason') or '')[:120]\n"
    "            parts.append(f'  - `{fn}`  (at {meta.get(\"quarantined_at\")}; {reason})')\n"
    "        parts.append('')\n"
    "\n"
    "    # Files still under retry budget\n"
    "    r = snap.get('file_retries', {})\n"
    "    if r:\n"
    "        parts.append(f'### Files failing Gate 8 (retry budget = {gqs.MAX_REBUILDS})')\n"
    "        parts.append('If you propose a rebuild, you MUST include spec references and')\n"
    "        parts.append('explicit constraints that address the last_error; do not submit')\n"
    "        parts.append('a generic rebuild of the same directive.')\n"
    "        for fn, meta in list(r.items())[:20]:\n"
    "            attempts = meta.get('attempts', 0)\n"
    "            last_err = (meta.get('last_error') or '')[:140]\n"
    "            parts.append(f'  - `{fn}`  attempts={attempts}/{gqs.MAX_REBUILDS}  '\n"
    "                         f'last_error: {last_err}')\n"
    "        parts.append('')\n"
    "\n"
    "    # Recent cohort summary (operator visibility; generator may use for context)\n"
    "    cohorts = snap.get('recent_cohorts', [])\n"
    "    if cohorts:\n"
    "        parts.append('### Recent cohort fail rates (last 5)')\n"
    "        for c in cohorts[-5:]:\n"
    "            parts.append(f'  - {c.get(\"id\")}: size={c.get(\"size\")}  '\n"
    "                         f'fail={c.get(\"fail_rate\", 0):.0%}')\n"
    "        parts.append('')\n"
    "\n"
    "    if not q and not r:\n"
    "        parts.append('- no failing files in retry accounting')\n"
    "        parts.append('- no quarantined files')\n"
    "\n"
    "    return '\\n'.join(parts)\n"
    "\n"
    "\n"
    "# \u2500\u2500 Combined entry point for generator to call "
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "\n"
    "def assemble_layer1_context() -> dict[str, str]:\n"
    "    \"\"\"Returns a dict of prompt-ready sections. Generator inserts by key.\"\"\"\n"
    "    return {\n"
    "        \"product_spec\":  load_product_spec(),\n"
    "        \"wiring_map\":    live_wiring_map(),\n"
    "        \"gaps_map\":      live_gaps_map(),\n"
    "        \"quality_map\":   live_quality_map(),\n"
    "    }"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("directive_knowledge_sources: add live_quality_map (commit 2)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "def live_quality_map(" in src and '"quality_map"' in src:
        print("  [skip] live_quality_map + assemble key already present")
        return 0

    if B_OLD not in src:
        print("  [FAIL] assemble_layer1_context anchor not found verbatim")
        print("  -- the file may have been edited; inspect manually")
        return 2

    src = src.replace(B_OLD, B_NEW, 1)
    print("  [patch] live_quality_map() inserted and wired into assemble_layer1_context")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify:")
    print("  python3 /home/workspace/zo_sentinel/directive_knowledge_sources.py 2>&1 | less")
    print("  # You should see a new '===== quality_map =====' section in self-test output")
    return 0


if __name__ == "__main__":
    sys.exit(main())