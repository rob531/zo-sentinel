#!/usr/bin/env python3
"""
patch_write_service_invalidation_selfkill_and_checkpoint.py

Commit 3 — write_service hardening. Two surgical additions:

  A. Invalidation self-kill. When _flush() catches a 'database has been
     invalidated' exception, the service logs it and calls os._exit(42)
     so the wrapper respawns. Today the poisoned process keeps accepting
     requests and returning fatal errors forever — that's the 5-hour
     outage pattern we saw on 2026-04-19 ~03:00 EST.

  B. Periodic CHECKPOINT. The _loop idle path (queue.Empty branch) calls
     self._con.execute('CHECKPOINT') every N flushes of idle time. Keeps
     WAL small so unclean shutdown has less to replay, reducing the odds
     that the next restart hits the same nullptr deref.

Non-destructive. Idempotent (marker-guarded). Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/write_service.py")

# ── Patch A: invalidation self-kill in _flush's exception handler ──────

A_OLD = (
    "        except Exception as e:\n"
    "            log.error(\"Batch commit failed: %s\", e)\n"
    "            try:\n"
    "                self._con.execute(\"ROLLBACK\")\n"
    "            except Exception:\n"
    "                pass\n"
    "            for item in batch:\n"
    "                if not item.event.is_set():\n"
    "                    item.error = e\n"
    "                    item.event.set()"
)
A_NEW = (
    "        except Exception as e:\n"
    "            log.error(\"Batch commit failed: %s\", e)\n"
    "            # Commit 3: detect DuckDB invalidation and self-kill so\n"
    "            # wrapper respawns cleanly. String match is intentional —\n"
    "            # DuckDB raises generic Exception with this text when the\n"
    "            # connection is poisoned after a fatal error.\n"
    "            err_str = str(e).lower()\n"
    "            if ('database has been invalidated' in err_str\n"
    "                    or 'invalidateddatabase' in err_str\n"
    "                    or 'dereference unique_ptr' in err_str):\n"
    "                log.error(\"FATAL: DuckDB invalidated; self-killing so \"\n"
    "                          \"wrapper can respawn. See write_service_wrapper.\")\n"
    "                # Release all waiting items first so callers don't hang\n"
    "                for item in batch:\n"
    "                    if not item.event.is_set():\n"
    "                        item.error = e\n"
    "                        item.event.set()\n"
    "                # Flush logs before bailing\n"
    "                import sys as _sys_exit\n"
    "                try:\n"
    "                    _sys_exit.stdout.flush()\n"
    "                    _sys_exit.stderr.flush()\n"
    "                except Exception:\n"
    "                    pass\n"
    "                os._exit(42)  # distinct code so wrapper knows why\n"
    "            try:\n"
    "                self._con.execute(\"ROLLBACK\")\n"
    "            except Exception:\n"
    "                pass\n"
    "            for item in batch:\n"
    "                if not item.event.is_set():\n"
    "                    item.error = e\n"
    "                    item.event.set()"
)

# ── Patch B: periodic CHECKPOINT on idle path ──────────────────────────
# We add an idle-tick counter to _loop. Every CHECKPOINT_EVERY_IDLE_TICKS
# consecutive queue.Empty hits, issue a CHECKPOINT. FLUSH_INTERVAL_S is 0.5s
# default, so default 120 ticks ≈ 60s of idle before first checkpoint.

B_OLD = (
    "        batch: list[_Item] = []\n"
    "        while self._running or not self._q.empty():\n"
    "            try:\n"
    "                item = self._q.get(timeout=FLUSH_INTERVAL_S)\n"
    "                if item is None:\n"
    "                    break\n"
    "                batch.append(item)\n"
    "                while len(batch) < BATCH_SIZE:\n"
    "                    try:\n"
    "                        nxt = self._q.get_nowait()\n"
    "                        if nxt is None:\n"
    "                            break\n"
    "                        batch.append(nxt)\n"
    "                    except queue.Empty:\n"
    "                        break\n"
    "            except queue.Empty:\n"
    "                pass\n"
    "\n"
    "            if batch:\n"
    "                self._flush(batch)\n"
    "                batch = []"
)
B_NEW = (
    "        batch: list[_Item] = []\n"
    "        # Commit 3: periodic CHECKPOINT to keep WAL small during idle\n"
    "        _idle_ticks = 0\n"
    "        _CHECKPOINT_EVERY_IDLE_TICKS = 120  # ~60s at FLUSH_INTERVAL_S=0.5\n"
    "        _CHECKPOINT_MIN_WRITES_SINCE = 50   # skip if nothing to checkpoint\n"
    "        _writes_since_last_checkpoint = 0\n"
    "        while self._running or not self._q.empty():\n"
    "            try:\n"
    "                item = self._q.get(timeout=FLUSH_INTERVAL_S)\n"
    "                if item is None:\n"
    "                    break\n"
    "                batch.append(item)\n"
    "                _idle_ticks = 0\n"
    "                while len(batch) < BATCH_SIZE:\n"
    "                    try:\n"
    "                        nxt = self._q.get_nowait()\n"
    "                        if nxt is None:\n"
    "                            break\n"
    "                        batch.append(nxt)\n"
    "                    except queue.Empty:\n"
    "                        break\n"
    "            except queue.Empty:\n"
    "                _idle_ticks += 1\n"
    "                if (_idle_ticks >= _CHECKPOINT_EVERY_IDLE_TICKS\n"
    "                        and _writes_since_last_checkpoint >= _CHECKPOINT_MIN_WRITES_SINCE):\n"
    "                    try:\n"
    "                        self._con.execute('CHECKPOINT')\n"
    "                        log.info('CHECKPOINT ok (after %d writes)',\n"
    "                                 _writes_since_last_checkpoint)\n"
    "                        _writes_since_last_checkpoint = 0\n"
    "                    except Exception as _ce:\n"
    "                        log.warning('CHECKPOINT failed: %s', _ce)\n"
    "                    _idle_ticks = 0\n"
    "\n"
    "            if batch:\n"
    "                _writes_since_last_checkpoint += len(batch)\n"
    "                self._flush(batch)\n"
    "                batch = []"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("write_service: invalidation self-kill + periodic CHECKPOINT (commit 3)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    patches = [
        ("A", "invalidation self-kill",  A_OLD, A_NEW, "DuckDB invalidated; self-killing"),
        ("B", "periodic CHECKPOINT",     B_OLD, B_NEW, "_CHECKPOINT_EVERY_IDLE_TICKS"),
    ]

    for label, desc, old, new, marker in patches:
        if marker in src:
            print(f"  [skip {label}] {desc}: already present")
            continue
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            print("  Inspect the relevant section of write_service.py by hand")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}: applied")
        changed = True

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
    print("\nVerify AST:")
    print('  python3 -c "import ast; ast.parse(open(\'/home/workspace/zo_mesh/write_service.py\').read()); print(\'AST OK\')"')
    print("\nRestart write_service (wrapper will pick up the new code):")
    print("  pkill -f 'write_service.py'")
    print("  # wrapper auto-respawns; watch log to confirm CHECKPOINT appears")
    print("  tail -f /home/workspace/logs/write_service.log | grep -E 'CHECKPOINT|invalidated'")
    print("\nExpected behavior:")
    print("  - After ~60s of idle with >=50 pending writes, log line: 'CHECKPOINT ok (after N writes)'")
    print("  - If DuckDB ever invalidates again: log 'FATAL: DuckDB invalidated'")
    print("    and process exits with code 42; wrapper respawns automatically")
    return 0


if __name__ == "__main__":
    sys.exit(main())