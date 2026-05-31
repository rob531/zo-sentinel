#!/usr/bin/env python3
"""
patch_write_service_concurrency.py -- serialize all DuckDB connection access in
write_service.py behind a single lock.

THE BUG (HTTP 500s + code-134 crashes):
  write_service.py keeps ONE duckdb connection (self._con) and uses it from:
    - the writer thread        (_loop -> _flush -> _write/_heartbeat, + CHECKPOINT)
    - the async HTTP handlers  (/query, /stats, /schema -> query_via_writer /
                                stats_from_db) which run on a DIFFERENT thread
  DuckDB connections are NOT thread-safe. A read (e.g. the activation governor /
  publisher polling build_artifact via /query) landing while the writer is
  mid-batch corrupts the heap:
    malloc(): unaligned tcache chunk detected -> Aborted (exit 134)
  The wrapper restarts the service, but every in-flight write 500s / gets
  Connection-refused during the ~20s gap, so build_artifact/provenance rows are
  lost -> the governor sits at comparable=0 and the publisher has nothing.
  This got worse exactly as the trio came online and started hammering /query.
  (self._lock already exists but only guards the _seq counter, not the cursor.)

THE FIX:
  Add self._con_lock and route EVERY self._con user through it. Done with a
  rename-wrapper per method so the bodies stay byte-identical: the public method
  acquires the lock and calls the renamed *_locked body. The writer thread and
  the HTTP handlers now mutually exclude. No re-entrancy (no *_locked method
  calls another locked method; _write's _next_id uses the separate self._lock).

Usage (on ZoComputer):
    python3 patch_write_service_concurrency.py            # patch in place (.bak)
    python3 patch_write_service_concurrency.py --dry-run
    python3 patch_write_service_concurrency.py --file /path/to/write_service.py
Then: pkill -f write_service.py   (wrapper relaunches the patched one)
And persist it (survives reboot):
    cd /home/workspace/zo_mesh && git add write_service.py && \
      git -c user.email=zo@zocomputer.io -c user.name=ZoComputer commit -m "..."

Idempotent: if self._con_lock is already present, changes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/write_service.py"

# Each tuple: (label, old, new). Rename-wrapper pattern -- only the def line +
# first body line change; the original body is preserved verbatim after it.
PATCHES = [
    # 1. the lock itself
    ("con_lock field",
     "        self._seq = 0\n"
     "        self._lock = threading.Lock()\n"
     "        self.total_written = 0\n",
     "        self._seq = 0\n"
     "        self._lock = threading.Lock()\n"
     "        # DuckDB connections are NOT thread-safe: the writer thread and the\n"
     "        # HTTP query handlers share self._con. Serialize ALL cursor access\n"
     "        # through this lock or the heap corrupts (malloc tcache abort 134).\n"
     "        self._con_lock = threading.Lock()\n"
     "        self.total_written = 0\n"),

    # 2. CHECKPOINT (inline, writer thread)
    ("checkpoint",
     "                        self._con.execute('CHECKPOINT')\n",
     "                        with self._con_lock:\n"
     "                            self._con.execute('CHECKPOINT')\n"),

    # 3. _flush (writer thread, whole BEGIN..COMMIT transaction must be atomic)
    ("_flush",
     "    def _flush(self, batch: list[_Item]):\n"
     "        try:\n"
     "            self._con.execute(\"BEGIN\")\n",
     "    def _flush(self, batch: list[_Item]):\n"
     "        with self._con_lock:\n"
     "            self._flush_locked(batch)\n"
     "\n"
     "    def _flush_locked(self, batch: list[_Item]):\n"
     "        try:\n"
     "            self._con.execute(\"BEGIN\")\n"),

    # 4. _heartbeat (writer thread)
    ("_heartbeat",
     "    def _heartbeat(self, status: str):\n"
     "        try:\n",
     "    def _heartbeat(self, status: str):\n"
     "        with self._con_lock:\n"
     "            self._heartbeat_locked(status)\n"
     "\n"
     "    def _heartbeat_locked(self, status: str):\n"
     "        try:\n"),

    # 5. _init_schema (writer thread, startup -- can race an early query)
    ("_init_schema",
     "    def _init_schema(self):\n"
     "        errors = []\n",
     "    def _init_schema(self):\n"
     "        with self._con_lock:\n"
     "            self._init_schema_locked()\n"
     "\n"
     "    def _init_schema_locked(self):\n"
     "        errors = []\n"),

    # 6. query_via_writer (HTTP handler thread)
    ("query_via_writer",
     "    def query_via_writer(self, sql: str, params: list, limit: int) -> dict:\n"
     "        safe = sql.rstrip(\";\")\n",
     "    def query_via_writer(self, sql: str, params: list, limit: int) -> dict:\n"
     "        with self._con_lock:\n"
     "            return self._query_via_writer_locked(sql, params, limit)\n"
     "\n"
     "    def _query_via_writer_locked(self, sql: str, params: list, limit: int) -> dict:\n"
     "        safe = sql.rstrip(\";\")\n"),

    # 7. stats_from_db (HTTP handler thread)
    ("stats_from_db",
     "    def stats_from_db(self) -> dict:\n"
     "        counts = {}\n",
     "    def stats_from_db(self) -> dict:\n"
     "        with self._con_lock:\n"
     "            return self._stats_from_db_locked()\n"
     "\n"
     "    def _stats_from_db_locked(self) -> dict:\n"
     "        counts = {}\n"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    src = path.read_text(encoding="utf-8")

    if "_con_lock" in src:
        print("Already patched (self._con_lock present). No change.")
        return 0

    out, applied, missing = src, [], []
    for label, old, new in PATCHES:
        if old in out:
            out = out.replace(old, new, 1)
            applied.append(label)
        else:
            missing.append(label)

    for m in missing:
        print(f"WARN: '{m}' anchor not found verbatim -- skipped (version drift?)")
    if missing:
        print(f"ERROR: {len(missing)} anchor(s) missing; refusing partial patch.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] would apply {applied} to {path}")
        return 0

    backup = path.with_suffix(path.suffix + ".bak2")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Patched {applied} in {path} (backup: {backup})")
    print("Now run:  pkill -f write_service.py   (wrapper relaunches the patched one)")
    print("Then persist:  cd /home/workspace/zo_mesh && git add write_service.py && commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
