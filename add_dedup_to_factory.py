import ast, re
from pathlib import Path

p = Path("/home/workspace/zo_sentinel/directive_factory.py")
c = p.read_text()

# Add get_existing_tasks() before get_next_seq()
old = 'def get_next_seq(queue_dir):'
new = '''def get_existing_tasks(queue_dir):
    """Parse all existing directive JSONs and return set of already-queued task names.
    Checks both active (.json) and completed (.done.json) files.
    Run factory repeatedly -- only net-new tasks get written.
    """
    import glob, json
    existing = set()
    for pattern in ['*.json', '*.done.json']:
        for fpath in glob.glob(os.path.join(queue_dir, pattern)):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                if 'task' in data:
                    existing.add(data['task'])
            except Exception:
                pass
    return existing


def get_next_seq(queue_dir):'''

if old in c:
    c = c.replace(old, new)
    print('[OK] get_existing_tasks() added')
else:
    print('[!!] anchor not found')
    raise SystemExit(1)

# Wire dedup into generate_directives()
old_gen = '    seq = get_next_seq(QUEUE_DIR)\n    for d in NEW_DIRECTIVES:'
new_gen = '''    seq = get_next_seq(QUEUE_DIR)
    existing = get_existing_tasks(QUEUE_DIR)
    skipped = 0
    for d in NEW_DIRECTIVES:
        if d['task'] in existing:
            print('[SKIP] already queued:', d['task'])
            skipped += 1
            continue'''

if old_gen in c:
    c = c.replace(old_gen, new_gen)
    print('[OK] dedup wired into generate_directives()')
else:
    print('[!!] generate loop anchor not found')
    raise SystemExit(1)

# Update final print to show skipped count
c = c.replace(
    "print('All complexity=high -> MiniMax primary')",
    "print('Skipped (already queued):', skipped)\n    print('All handler=generate_file | high -> MiniMax primary')"
)

try:
    ast.parse(c)
    print('[OK] syntax passed')
except SyntaxError as e:
    print('[!!]', e.lineno, e.msg)
    raise

p.write_text(c)
print('[OK] directive_factory.py updated with idempotent deduplication')