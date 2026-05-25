#!/usr/bin/env python3
"""
patch_directive_knowledge_sources_add_quality_map_v2.py

Retry of the commit-2 patcher. The v1 anchor used a unicode box-drawing
header line that didn't match the file verbatim. This v2 uses a minimal,
unicode-free anchor: the `return { ... }` block of assemble_layer1_context
itself. Much harder to drift.

Single-patch approach:
  - Insert live_quality_map() BEFORE assemble_layer1_context
  - Extend the return dict to include 'quality_map' key

Idempotent (checks for 'def live_quality_map' marker). AST-validated.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/directive_knowledge_sources.py")

# Minimal anchor: the return statement of assemble_layer1_context.
# No unicode, no header comments. Just the function body.
OLD = (
    'def assemble_layer1_context() -> dict[str, str]:\n'
    '    """Returns a dict of prompt-ready sections. Generator inserts by key."""\n'
    '    return {\n'
    '        "product_spec":  load_product_spec(),\n'
    '        "wiring_map":    live_wiring_map(),\n'
    '        "gaps_map":      live_gaps_map(),\n'
    '    }'
)

NEW = (
    'def live_quality_map() -> str:\n'
    '    """Pull Gate 8 breaker state, quarantined files, and retry budgets.\n'
    '    Generator uses this to avoid re-proposing known-bad rebuilds.\n'
    '    Degrades gracefully if gate_quality_state is unavailable.\n'
    '    """\n'
    '    try:\n'
    '        import sys as _sys\n'
    "        if '/home/workspace/zo_sentinel' not in _sys.path:\n"
    "            _sys.path.insert(0, '/home/workspace/zo_sentinel')\n"
    '        import gate_quality_state as gqs\n'
    '        snap = gqs.snapshot()\n'
    '    except Exception as e:\n'
    '        return (\n'
    "            '## Quality map (Gate 8 breaker + quarantine)\\n'\n"
    "            f'- state unavailable: {e}\\n'\n"
    "            '- generator should proceed normally; do not treat this as a signal\\n'\n"
    '        )\n'
    '\n'
    "    parts = ['## Quality map (Gate 8 circuit breaker + quarantine)']\n"
    "    state = snap.get('state', 'closed')\n"
    "    parts.append(f'- breaker_state: **{state}**')\n"
    "    if snap.get('state_changed_reason'):\n"
    "        parts.append(f'  (since {snap.get(\"state_changed_at\")}: '\n"
    "                     f'{snap.get(\"state_changed_reason\")})')\n"
    "    parts.append('')\n"
    '\n'
    "    if state == 'tripped':\n"
    "        parts.append('### !! BREAKER TRIPPED !!')\n"
    "        parts.append('Generator MUST NOT propose rebuilds of any file listed')\n"
    "        parts.append('under retry_budget or quarantine below. New/unrelated')\n"
    "        parts.append('directives are still OK. Human must run reset_breaker.py')\n"
    "        parts.append('to re-enable rebuilds.')\n"
    "        parts.append('')\n"
    "    elif state == 'half-open':\n"
    "        parts.append('### breaker half-open')\n"
    "        parts.append('Rebuilds permitted for ONE batch. Prefer conservative')\n"
    "        parts.append('changes with explicit spec references.')\n"
    "        parts.append('')\n"
    '\n'
    "    q = snap.get('quarantined', {})\n"
    '    if q:\n'
    "        parts.append('### Quarantined files (DO NOT propose rebuilds)')\n"
    "        for fn, meta in list(q.items())[:20]:\n"
    "            reason = (meta.get('reason') or '')[:120]\n"
    "            parts.append(f'  - `{fn}`  (at {meta.get(\"quarantined_at\")}; {reason})')\n"
    "        parts.append('')\n"
    '\n'
    "    r = snap.get('file_retries', {})\n"
    '    if r:\n'
    "        parts.append(f'### Files failing Gate 8 (retry budget = {gqs.MAX_REBUILDS})')\n"
    "        parts.append('If proposing a rebuild, you MUST reference the listed')\n"
    "        parts.append('last_error and relevant spec section explicitly.')\n"
    "        for fn, meta in list(r.items())[:20]:\n"
    "            attempts = meta.get('attempts', 0)\n"
    "            last_err = (meta.get('last_error') or '')[:140]\n"
    "            parts.append(f'  - `{fn}`  attempts={attempts}/{gqs.MAX_REBUILDS}  '\n"
    "                         f'last_error: {last_err}')\n"
    "        parts.append('')\n"
    '\n'
    "    cohorts = snap.get('recent_cohorts', [])\n"
    '    if cohorts:\n'
    "        parts.append('### Recent cohort fail rates (last 5)')\n"
    "        for c in cohorts[-5:]:\n"
    "            parts.append(f'  - {c.get(\"id\")}: size={c.get(\"size\")}  '\n"
    "                         f'fail={c.get(\"fail_rate\", 0):.0%}')\n"
    "        parts.append('')\n"
    '\n'
    '    if not q and not r:\n'
    "        parts.append('- no failing files in retry accounting')\n"
    "        parts.append('- no quarantined files')\n"
    '\n'
    "    return '\\n'.join(parts)\n"
    '\n'
    '\n'
    'def assemble_layer1_context() -> dict[str, str]:\n'
    '    """Returns a dict of prompt-ready sections. Generator inserts by key."""\n'
    '    return {\n'
    '        "product_spec":  load_product_spec(),\n'
    '        "wiring_map":    live_wiring_map(),\n'
    '        "gaps_map":      live_gaps_map(),\n'
    '        "quality_map":   live_quality_map(),\n'
    '    }'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("directive_knowledge_sources: add live_quality_map (v2, minimal anchor)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "def live_quality_map(" in src and '"quality_map"' in src:
        print("  [skip] live_quality_map + assemble key already present")
        return 0

    if OLD not in src:
        print("  [FAIL] assemble_layer1_context anchor still not found verbatim")
        print("         tail of file for inspection:")
        for line in src.splitlines()[-30:]:
            print(f"         | {line}")
        return 2

    src = src.replace(OLD, NEW, 1)
    print("  [patch] live_quality_map inserted; return dict extended")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify:")
    print("  python3 /home/workspace/zo_sentinel/directive_knowledge_sources.py 2>&1 | grep -A 2 'quality_map'")
    return 0


if __name__ == "__main__":
    sys.exit(main())