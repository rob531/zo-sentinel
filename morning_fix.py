#!/usr/bin/env python3
"""Fix _builds_this_session scoping bug + add last_scanned column"""
import ast, requests
from pathlib import Path

# 1. Fix schema
try:
    r = requests.post('http://127.0.0.1:8772/execute',
        json={'sql': 'ALTER TABLE mcp_server_registry ADD COLUMN IF NOT EXISTS last_scanned TIMESTAMPTZ', 'wait': True},
        timeout=10)
    print('[schema] last_scanned column:', 'OK' if r.status_code == 200 else r.text)
except Exception as e:
    print('[schema] write_service not up yet:', e)

# 2. Fix builder scoping bug
p = Path('/home/workspace/zo_mesh/zo_sentinel_builder.py')
c = p.read_text()
orig = c

# The bug: global declaration inside run_cycle() but counters declared
# at module level after the function definition, causing UnboundLocalError
# Fix: ensure module-level initialisation appears before run_cycle def

# Check if already correct
if '_cycle_count = 0' in c and '_builds_this_session = 0' in c:
    # Verify they are module-level (not inside a function)
    lines = c.split('\n')
    for i, line in enumerate(lines):
        if '_cycle_count = 0' in line and not line.startswith(' '):
            print('[builder] module-level counters already present at line', i+1)
            break
    else:
        print('[builder] counters found but may be misplaced -- checking...')

# Find and fix: move counters to just before run_cycle def
OLD = '_cycle_count = 0\n_builds_this_session = 0\n\n\ndef run_cycle():'
if OLD in c:
    print('[builder] counter placement looks correct')
else:
    # They may be inside the function or missing -- add them before run_cycle
    if 'def run_cycle():' in c and '_cycle_count = 0' not in c:
        c = c.replace('def run_cycle():', '_cycle_count = 0\n_builds_this_session = 0\n\n\ndef run_cycle():')
        print('[builder] added module-level counters before run_cycle')
    elif 'def run_cycle():' in c:
        # Counters exist somewhere else, ensure global line is present
        if 'global _cycle_count, _builds_this_session' not in c:
            c = c.replace(
                'def run_cycle():',
                'def run_cycle():\n    global _cycle_count, _builds_this_session'
            )
            print('[builder] added global declaration inside run_cycle')
        else:
            print('[builder] global declaration already present')

try:
    ast.parse(c)
    print('[OK] syntax check passed')
except SyntaxError as e:
    print('[!!] syntax error', e.lineno, e.msg)
    raise

if c != orig:
    p.write_text(c)
    print('[OK] builder patched')
else:
    print('[--] no changes needed')

print('\nNext: zm go')