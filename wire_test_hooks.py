#!/usr/bin/env python3
"""
wire_test_hooks.py -- Patch zo_sentinel_builder.py with test lifecycle hooks.

Wires 5 hook points:
  1. Import builder_test_hooks
  2. on_phase_checkpoint after run_phase_checkpoint()
  3. on_queue_empty when no directives
  4. on_build_success + reset_fix_attempts after clean build
  5. on_rescue_success after rescue build
"""
import ast
from pathlib import Path

p = Path('/home/workspace/zo_mesh/zo_sentinel_builder.py')
c = p.read_text()
orig = c

# 1. Import hooks (after logging setup, before first usage)
if 'builder_test_hooks' not in c:
    hook_import = (
        'sys.path.insert(0, "/home/workspace/zo_sentinel")\n'
        'try:\n'
        '    import builder_test_hooks as _hooks\n'
        'except Exception as _hook_err:\n'
        '    _hooks = None\n\n'
    )
    # Insert before logging.basicConfig
    c = c.replace('logging.basicConfig(', hook_import + 'logging.basicConfig(', 1)
    print('[1] hooks import added')
else:
    print('[1] hooks already imported')

# 2. Add cycle/session counters before run_cycle def
if '_cycle_count' not in c:
    c = c.replace(
        'def run_cycle():',
        '_cycle_count = 0\n_builds_this_session = 0\n\n\ndef run_cycle():\n    global _cycle_count, _builds_this_session\n    _cycle_count += 1',
        1
    )
    print('[2] cycle counters added')
else:
    print('[2] cycle counters already present')

# 3. Wire on_phase_checkpoint inside run_phase_checkpoint
old_phase = '    log.info("  %s passed=%s failed=%s", status, passed, failed)'
new_phase = (
    '    log.info("  %s passed=%s failed=%s", status, passed, failed)\n'
    '    if _hooks:\n'
    '        try: _hooks.on_phase_checkpoint(phase, passed, failed)\n'
    '        except Exception as _he: log.warning("[hook] phase_checkpoint: %s", _he)'
)
if old_phase in c and 'on_phase_checkpoint' not in c:
    c = c.replace(old_phase, new_phase)
    print('[3] on_phase_checkpoint wired')
else:
    print('[3] phase checkpoint -- skipped (already wired or not found)')

# 4. Wire on_queue_empty in run_cycle when no directives
# Find the "No pending directives" log line
no_dir_patterns = [
    '        log.info("No pending directives.")\n        return',
    '        log.info("No pending directives.")\n        evict_ollama()\n        return',
]
for pat in no_dir_patterns:
    if pat in c and 'on_queue_empty' not in c:
        new_idle = pat.replace(
            '        return',
            '        if _hooks:\n'
            '            try: _hooks.on_queue_empty(cycle_count=_cycle_count,\n'
            '                                       builds_total=_builds_this_session)\n'
            '            except Exception as _he: log.warning("[hook] queue_empty: %s", _he)\n'
            '        return'
        )
        c = c.replace(pat, new_idle)
        print('[4] on_queue_empty wired')
        break
else:
    if 'on_queue_empty' in c:
        print('[4] on_queue_empty already wired')
    else:
        print('[4] WARN: could not find idle return pattern -- manual wire needed')

# 5. Wire on_build_success + reset_fix_attempts after first build_complete event
old_build = (
    '        mesh_event("build_complete", {"task": task, "file": str(output_path),\n'
    '                                      "bytes": len(code), "wiring_warnings": wiring_warns})'
)
new_build = (
    '        mesh_event("build_complete", {"task": task, "file": str(output_path),\n'
    '                                      "bytes": len(code), "wiring_warnings": wiring_warns})\n'
    '        _builds_this_session += 1\n'
    '        if _hooks:\n'
    '            try:\n'
    '                _hooks.on_build_success(task, str(output_path))\n'
    '                _hooks.reset_fix_attempts(Path(output_path).name)\n'
    '            except Exception as _he: log.warning("[hook] build_success: %s", _he)'
)
if old_build in c and 'on_build_success' not in c:
    c = c.replace(old_build, new_build, 1)  # first occurrence only
    print('[5] on_build_success + reset_fix_attempts wired')
else:
    print('[5] build_success -- skipped (already wired or not found)')

# 6. Wire on_rescue_success after rescue build_complete
old_rescue = (
    '        mesh_event("build_complete", {"task": task, "file": str(output_path),\n'
    '                                      "bytes": len(code), "rescue": True})'
)
new_rescue = (
    '        mesh_event("build_complete", {"task": task, "file": str(output_path),\n'
    '                                      "bytes": len(code), "rescue": True})\n'
    '        _builds_this_session += 1\n'
    '        if _hooks:\n'
    '            try:\n'
    '                _hooks.on_rescue_success(task, str(output_path))\n'
    '                _hooks.reset_fix_attempts(Path(output_path).name)\n'
    '            except Exception as _he: log.warning("[hook] rescue_success: %s", _he)'
)
if old_rescue in c and 'on_rescue_success' not in c:
    c = c.replace(old_rescue, new_rescue)
    print('[6] on_rescue_success wired')
else:
    print('[6] rescue hook -- skipped (already wired or not found)')

# Syntax check
try:
    ast.parse(c)
    print('[OK] syntax check passed')
except SyntaxError as e:
    print(f'[!!] syntax error line {e.lineno}: {e.msg}')
    p.write_text(orig)
    raise

if c != orig:
    p.write_text(c)
    print('[OK] builder patched')
else:
    print('[--] no changes')