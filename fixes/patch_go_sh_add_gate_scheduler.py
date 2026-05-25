#!/usr/bin/env python3
"""
patch_go_sh_add_gate_scheduler.py

Add gate_scheduler to go.sh as a nohup-managed daemon, matching the
pattern used by all other mesh processes. Replaces the previous
supervisord-managed approach which silently disappeared on ZoComputer
reboots when /etc/zo/supervisord-user.conf got wiped.

Four edits:
  A. Append 'gate_scheduler.py' to the kill-list in section 1
  B. Add a new section 12.6 that nohup-launches gate_scheduler.py
     (placed between 12.5 DirectiveGenerator and 13 WorldArticleFeeder)
  C. Add 'GateScheduler' line to the SUMMARY block
  D. Add a note under 'Key commands' pointing at the gate runs log dir

Anchors built via string concatenation, not triple-quoted blocks -- the
go.sh content contains bash double quotes which collide with Python
triple-quote boundaries. Lesson learned the hard way on the first
attempt.

Idempotent. bash -n syntax check on candidate before promotion. Backup.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")

# ---- Patch A: append gate_scheduler.py to kill-list ----------------------
A_OLD = (
    "            world_article_feeder.py zo_sentinel_builder.py \\\n"
    "            sentinel_directive_generator.py; do"
)
A_NEW = (
    "            world_article_feeder.py zo_sentinel_builder.py \\\n"
    "            sentinel_directive_generator.py gate_scheduler.py; do"
)

# ---- Patch B: new section 12.6 ------------------------------------------
# Anchor: the last line of section 12.5 (DirectiveGenerator) followed by
# the opening of section 13 (WorldArticleFeeder). Insert 12.6 between.
B_OLD = (
    '[[ -n "$SDG" ]] && ok "DirectiveGenerator PID $SDG (MINIMAX: '
    '$([[ -n "$MINIMAX_API_KEY" ]] && echo SET || echo NOT_SET))" '
    '|| warn "DirectiveGenerator failed to start"\n'
    '\n'
    'hdr "13. World Article Feeder"'
)
B_NEW = (
    '[[ -n "$SDG" ]] && ok "DirectiveGenerator PID $SDG (MINIMAX: '
    '$([[ -n "$MINIMAX_API_KEY" ]] && echo SET || echo NOT_SET))" '
    '|| warn "DirectiveGenerator failed to start"\n'
    '\n'
    'hdr "12.6 Gate Scheduler (6h cadence, nohup)"\n'
    '# Invokes /home/workspace/zo_sentinel/tests/gates/run_gates_periodic.py\n'
    '# every 21600s (6h). On startup runs immediately so post-reboot we\n'
    '# get a fresh signal within ~60s. Gate 8 needs mesh_memory, so\n'
    '# write_service (section 3) and builder history must be available first.\n'
    '# Replaced supervisord management -- supervisord config was being wiped\n'
    '# on ZoComputer reboots, leaving gate_scheduler silently absent.\n'
    'nohup python3 $SENTINEL/gate_scheduler.py >> $LOGS/gate_scheduler.log 2>&1 &\n'
    'sleep 2\n'
    "GSC=$(pgrep -f 'gate_scheduler.py' 2>/dev/null | head -1)\n"
    '[[ -n "$GSC" ]] && ok "GateScheduler PID $GSC" || warn "GateScheduler failed to start"\n'
    '\n'
    'hdr "13. World Article Feeder"'
)

# ---- Patch C: SUMMARY block ---------------------------------------------
C_OLD = (
    'echo "  DirectiveGen:    '
    "$(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | wc -l) "
    'instance(s) [nohup]"'
)
C_NEW = (
    'echo "  DirectiveGen:    '
    "$(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | wc -l) "
    'instance(s) [nohup]"\n'
    'echo "  GateScheduler:   '
    "$(pgrep -f 'gate_scheduler.py' 2>/dev/null | wc -l) "
    'instance(s) [nohup, 6h cadence]"'
)

# ---- Patch D: add a key-commands hint -----------------------------------
D_OLD = 'echo "    Builder log: less +F $LOGS/zo_sentinel_builder.log"'
D_NEW = (
    'echo "    Builder log: less +F $LOGS/zo_sentinel_builder.log"\n'
    'echo "    Gate runs:   ls -lt /home/workspace/logs/gate_runs/ | head"'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def _bash_syntax_ok(path):
    try:
        r = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True, ""
        return False, r.stderr.strip()[:300]
    except Exception as e:
        return False, f"bash -n invocation failed: {e}"


def main():
    print("=" * 60)
    print("go.sh: add gate_scheduler via nohup (replaces supervisord)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    patches = [
        ("A", "kill-list entry",            A_OLD, A_NEW, "gate_scheduler.py; do"),
        ("B", "section 12.6 launch block",  B_OLD, B_NEW, "12.6 Gate Scheduler"),
        ("C", "SUMMARY GateScheduler line", C_OLD, C_NEW, "GateScheduler:"),
        ("D", "key-commands gate_runs hint", D_OLD, D_NEW, "Gate runs:   ls -lt"),
    ]

    for label, desc, old, new, marker in patches:
        if marker in src:
            print(f"  [skip {label}] {desc}: already present")
            continue
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}: applied")
        changed = True

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    tmp = TARGET.with_suffix(".sh.candidate")
    tmp.write_text(src)
    ok, err = _bash_syntax_ok(tmp)
    if not ok:
        print(f"\n  [FAIL] bash -n rejected candidate:\n{err}")
        tmp.unlink()
        return 2
    tmp.unlink()

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched (bash syntax OK)")
    print("\nVerify:")
    print("  bash -n /home/workspace/zo_mesh/go.sh && echo 'syntax OK'")
    print("  grep -n 'gate_scheduler' /home/workspace/zo_mesh/go.sh")
    print("")
    print("Resurrect the scheduler NOW without waiting for a reboot:")
    print("  nohup python3 /home/workspace/zo_sentinel/gate_scheduler.py \\")
    print("    >> /home/workspace/logs/gate_scheduler.log 2>&1 &")
    print("  sleep 3")
    print("  pgrep -f gate_scheduler.py")
    print("  tail -10 /home/workspace/logs/gate_scheduler.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())