#!/usr/bin/env python3
# Patch search_api.py: single-term LIKE -> per-word AND ILIKE
import ast
from pathlib import Path

p = Path('/home/workspace/zo_sentinel/search_api.py')
c = p.read_text()
orig = c

old = '''    if q:
        conditions.append("(name ILIKE ? OR description ILIKE ?)")
        search_pattern = f"%{q}%"
        params.extend([search_pattern, search_pattern])'''

new = '''    if q:
        # Multi-term AND search: each word must appear in name OR description
        terms = [t.strip() for t in q.split() if t.strip()]
        for term in terms:
            conditions.append("(name ILIKE ? OR description ILIKE ?)")
            pattern = f"%{term}%"
            params.extend([pattern, pattern])'''

if old in c:
    c = c.replace(old, new)
    print('[OK] multi-term search patch applied')
else:
    print('[!!] search block not found -- check manually')
    exit(1)

try:
    ast.parse(c)
    print('[OK] syntax check passed')
except SyntaxError as e:
    print('[!!] syntax error', e)
    p.write_text(orig)
    raise

p.write_text(c)
print('[OK] written')
print('Restart search_api.py to apply: pkill -f search_api.py && nohup python3 /home/workspace/zo_sentinel/search_api.py &')