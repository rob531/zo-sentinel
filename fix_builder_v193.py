#!/usr/bin/env python3
"""
fix_builder_v193.py -- Structural patch for zo_sentinel_builder.py

Fixes two inter-related bugs that cause infinite directive looping:

  BUG 1 (root cause):
    In build_task_generate_file(), `_builds_this_session += 1` is treated
    as a local variable assignment by Python (because of the +=), but the
    local is never initialised. Python 3.12 raises:
      UnboundLocalError: cannot access local variable '_builds_this_session'
      where it is not associated with a value
    Fix: add `global _builds_this_session` at the top of the function.

  BUG 2 (amplifier):
    process_directive() calls mark_directive_done(directive) INSIDE the
    try block, AFTER fn(directive). When fn() raises (Bug 1), the exception
    jumps to except, skipping mark_directive_done entirely. The directive
    file is never renamed to .done.json, so every poll cycle reloads and
    re-processes it -- producing the observed infinite loop.
    Fix: move mark_directive_done() to a finally: block so it always runs.

Version bump: v1.9.2 -> v1.9.3
"""
import ast, sys
from pathlib import Path

BUILDER = Path("/home/workspace/zo_mesh/zo_sentinel_builder.py")
BACKUP  = Path("/home/workspace/zo_mesh/zo_sentinel_builder.v192.bak.py")

print("=" * 60)
print("ZO-SENTINEL Builder patch: v1.9.2 -> v1.9.3")
print("=" * 60)

original = BUILDER.read_text()

# ── BACKUP ──────────────────────────────────────────────────────────────────
BACKUP.write_text(original)
print(f"[OK] Backup: {BACKUP}")

content = original

# ── FIX 1: global _builds_this_session in build_task_generate_file ──────────
OLD_FN_HEADER = '''def build_task_generate_file(directive: dict) -> bool:
    task        = directive.get("task", "unknown")
    description = directive.get("description", "")
    output_file = directive.get("output_file", task + ".py")
    complexity  = directive.get("complexity", "medium")
    extra_ctx   = directive.get("context", "")
    reads       = directive.get("reads", [])
    phase       = directive.get("phase", "?")
    output_path = PROJECT_DIR / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)'''

NEW_FN_HEADER = '''def build_task_generate_file(directive: dict) -> bool:
    global _builds_this_session  # v1.9.3: declare global to prevent UnboundLocalError
    task        = directive.get("task", "unknown")
    description = directive.get("description", "")
    output_file = directive.get("output_file", task + ".py")
    complexity  = directive.get("complexity", "medium")
    extra_ctx   = directive.get("context", "")
    reads       = directive.get("reads", [])
    phase       = directive.get("phase", "?")
    output_path = PROJECT_DIR / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)'''

if OLD_FN_HEADER in content:
    content = content.replace(OLD_FN_HEADER, NEW_FN_HEADER, 1)
    print("[OK] Fix 1 applied: global _builds_this_session declared")
else:
    print("[!!] Fix 1 FAILED: could not locate build_task_generate_file header")
    sys.exit(1)

# ── FIX 2: move mark_directive_done to finally in process_directive ─────────
OLD_DISPATCH = '''    try:
        success = fn(directive)
        if success:
            inject_next_directive(directive)
            if phase not in _phase_tasks_done: _phase_tasks_done[phase] = set()
            if output: _phase_tasks_done[phase].add(output)
            expected = set(PHASE_CHECKPOINTS.get(phase, []))
            if expected and expected.issubset(_phase_tasks_done.get(phase, set())):
                run_phase_checkpoint(phase, list(expected))
        log.info("--- [%s]: %s ---", task, "OK" if success else "FAILED")
        mark_directive_done(directive)
    except Exception as e:
        log.error("Directive %s: %s", task, e)
        mesh_event("build_error", {"task": task, "error": str(e)}, severity="WARNING")'''

NEW_DISPATCH = '''    success = False  # v1.9.3: initialise before try so finally can read it
    try:
        success = fn(directive)
        if success:
            inject_next_directive(directive)
            if phase not in _phase_tasks_done: _phase_tasks_done[phase] = set()
            if output: _phase_tasks_done[phase].add(output)
            expected = set(PHASE_CHECKPOINTS.get(phase, []))
            if expected and expected.issubset(_phase_tasks_done.get(phase, set())):
                run_phase_checkpoint(phase, list(expected))
        log.info("--- [%s]: %s ---", task, "OK" if success else "FAILED")
    except Exception as e:
        log.error("Directive %s: %s", task, e)
        mesh_event("build_error", {"task": task, "error": str(e)}, severity="WARNING")
    finally:
        mark_directive_done(directive)  # v1.9.3: always runs, prevents infinite loop on exception'''

if OLD_DISPATCH in content:
    content = content.replace(OLD_DISPATCH, NEW_DISPATCH, 1)
    print("[OK] Fix 2 applied: mark_directive_done moved to finally block")
else:
    print("[!!] Fix 2 FAILED: could not locate process_directive try block")
    sys.exit(1)

# ── VERSION BUMP ─────────────────────────────────────────────────────────────
content = content.replace(
    "ZO-SENTINEL Builder v1.9.2",
    "ZO-SENTINEL Builder v1.9.3",
    1
)
print("[OK] Version bumped to v1.9.3")

# ── SYNTAX CHECK ─────────────────────────────────────────────────────────────
try:
    ast.parse(content)
    print("[OK] Syntax check: PASS")
except SyntaxError as e:
    print(f"[!!] SYNTAX ERROR: {e}")
    print("     Restoring backup...")
    BUILDER.write_text(original)
    sys.exit(1)

# ── WRITE ────────────────────────────────────────────────────────────────────
if content == original:
    print("[!!] No changes detected — check anchor strings above")
    sys.exit(1)

BUILDER.write_text(content)
print(f"[OK] Written: {BUILDER} ({len(content)} chars)")
print()
print("Next steps:")
print("  pkill -f zo_sentinel_builder.py")
print("  nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &")
print("  zm go    # if supervisord manages it")
print()
print("Expected after restart:")
print("  - auto_dependency_resolver builds once, marks done, never re-queued")
print("  - Any future exception in a directive handler still marks directive done")
print("  - _builds_this_session counter increments correctly")
print("[DONE] Patch applied successfully")