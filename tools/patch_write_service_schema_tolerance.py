#!/usr/bin/env python3
"""
patch_write_service_schema_tolerance.py -- stop the recurring write 500s caused
by caller/table schema mismatches.

THE NOISE (non-fatal, but a 500 per write):
  Write error [audit_log]: 'event_id'
  Write error [service_health]: Table "service_health" does not have a column
                                with name "detail"
Two distinct causes, both in write_service._write:
  1. DEFAULT-generated PK not supplied by the caller. audit_log's PK is
     event_id DEFAULT gen_random_uuid(); callers omit it; the MERGE-fix upsert
     does r[pk_col] -> KeyError 'event_id'. (Regression from
     patch_write_service_merge.py.)
  2. Caller sends a column the table doesn't have (e.g. service_health 'detail'
     vs the real 'meta'; goose's audit_log uses 'detail' vs 'details_json').
     The INSERT then hits a Binder error -> 500.
These drop audit_log / service_health rows (peripheral -- build_provenance is
unaffected, so the governor still gets evidence) but spam a 500 per build.

THE FIX (one place, future-proof):
  1. PK guard: add `and pk_col in r` to the upsert condition. When the PK isn't
     supplied, fall through to a plain INSERT and let the DEFAULT fill it.
  2. Column tolerance: drop row keys not in the table's real columns (cached
     PRAGMA table_info), with a log.warning. Writes then land with their valid
     fields instead of 500ing -- kills the mismatch noise from ANY caller.

Order-independent of the MERGE fix (#9, whose _write block this extends) and the
concurrency fix (#35, which touched different methods). Requires #9 applied.

Usage (on ZoComputer):
    python3 patch_write_service_schema_tolerance.py            # patch (.bak3)
    python3 patch_write_service_schema_tolerance.py --dry-run
    python3 patch_write_service_schema_tolerance.py --file /path/to/write_service.py
Then: pkill -f write_service.py   (wrapper relaunches the patched one)
And persist: cd /home/workspace/zo_mesh && git add write_service.py && commit

Idempotent: if _table_cols is already present, changes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/write_service.py"

PATCHES = [
    # 1. column cache field in __init__
    ("cols_cache field",
     "        self.total_written = 0\n"
     "        self.total_errors  = 0\n",
     "        self.total_written = 0\n"
     "        self.total_errors  = 0\n"
     "        self._cols_cache: dict = {}\n"),

    # 2. _table_cols helper, inserted just before _write
    ("_table_cols helper",
     "    def _write(self, item: _Item):\n",
     "    def _table_cols(self, table: str) -> set:\n"
     "        \"\"\"Cached real column names (PRAGMA table_info). Lets _write drop\n"
     "        payload keys the table lacks so a caller/schema mismatch lands its\n"
     "        valid fields instead of 500ing. Empty set (unknown table) => no drop.\"\"\"\n"
     "        c = self._cols_cache.get(table)\n"
     "        if c is None:\n"
     "            try:\n"
     "                info = self._con.execute(f\"PRAGMA table_info('{table}')\").fetchall()\n"
     "                c = {row[1] for row in info}\n"
     "            except Exception:\n"
     "                c = set()\n"
     "            self._cols_cache[table] = c\n"
     "        return c\n"
     "\n"
     "    def _write(self, item: _Item):\n"),

    # 3. drop unknown columns at the top of the per-row loop
    ("drop-unknown-columns",
     "        for row in item.rows:\n"
     "            r = {**row}\n"
     "            if \"id\" not in r and item.table not in _NO_AUTO_ID:\n",
     "        known_cols = self._table_cols(item.table)\n"
     "        for row in item.rows:\n"
     "            r = {**row}\n"
     "            if known_cols:\n"
     "                _unknown = [k for k in list(r) if k not in known_cols]\n"
     "                if _unknown:\n"
     "                    log.warning(\"Write [%s]: dropping unknown column(s) %s -- caller/schema mismatch\",\n"
     "                                item.table, _unknown)\n"
     "                    for _k in _unknown:\n"
     "                        del r[_k]\n"
     "            if \"id\" not in r and item.table not in _NO_AUTO_ID:\n"),

    # 4. PK guard on the upsert condition (DEFAULT-generated PK not supplied)
    ("pk-guard",
     "            if (item.mode == \"upsert\" or item.table in _TABLE_PK) and set_cols:\n",
     "            if (item.mode == \"upsert\" or item.table in _TABLE_PK) and set_cols and pk_col in r:\n"),
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

    if "_table_cols" in src:
        print("Already patched (_table_cols present). No change.")
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

    backup = path.with_suffix(path.suffix + ".bak3")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Patched {applied} in {path} (backup: {backup})")
    print("Now run:  pkill -f write_service.py   (wrapper relaunches the patched one)")
    print("Then persist:  cd /home/workspace/zo_mesh && git add write_service.py && commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
