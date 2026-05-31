#!/usr/bin/env python3
"""
patch_write_service_merge.py -- one-shot host patcher for the write_service
DuckDB MERGE-INTO 500 bug.

write_service.py upserts via `INSERT ... ON CONFLICT(pk) DO UPDATE SET ...`,
which DuckDB compiles to MERGE INTO and hits a `Binder::GenerateMergeInto`
internal assertion -> HTTP 500 on the heartbeat + every `_TABLE_PK` table.
This rewrites both upsert sites to an explicit exists->UPDATE / else INSERT (no
MERGE), which DuckDB handles fine.

Usage (on ZoComputer):
    python3 patch_write_service_merge.py            # patch in place (.bak written)
    python3 patch_write_service_merge.py --dry-run  # show what would change
    python3 patch_write_service_merge.py --file /path/to/write_service.py
Then: pkill -f write_service.py   (the wrapper relaunches the patched one)

Idempotent + safe: if the exact original blocks aren't found (already patched, or
a different version), it reports and changes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT = "/home/workspace/zo_mesh/write_service.py"

# --- site 1: _Writer._write upsert branch -----------------------------------
OLD_WRITE = '''            if item.mode == "upsert" or item.table in _TABLE_PK:
                up = ", ".join(f"{c}=excluded.{c}" for c in cols if c != pk_col)
                if up:
                    sql = (f"INSERT INTO {item.table} ({cs}) VALUES ({ph}) "
                           f"ON CONFLICT({pk_col}) DO UPDATE SET {up}")
                else:
                    sql = f"INSERT OR IGNORE INTO {item.table} ({cs}) VALUES ({ph})"
            else:
                sql = f"INSERT OR IGNORE INTO {item.table} ({cs}) VALUES ({ph})"

            self._con.execute(sql, vals)'''

NEW_WRITE = '''            set_cols = [c for c in cols if c != pk_col]
            if (item.mode == "upsert" or item.table in _TABLE_PK) and set_cols:
                # DuckDB ON CONFLICT DO UPDATE -> MERGE INTO -> Binder::GenerateMergeInto
                # internal assertion (HTTP 500). Explicit exists->UPDATE / else INSERT
                # (no MERGE) is handled correctly.
                hit = self._con.execute(
                    f"SELECT 1 FROM {item.table} WHERE {pk_col}=? LIMIT 1",
                    [r[pk_col]]).fetchone()
                if hit:
                    set_clause = ", ".join(f"{c}=?" for c in set_cols)
                    self._con.execute(
                        f"UPDATE {item.table} SET {set_clause} WHERE {pk_col}=?",
                        [r[c] for c in set_cols] + [r[pk_col]])
                else:
                    self._con.execute(
                        f"INSERT INTO {item.table} ({cs}) VALUES ({ph})", vals)
            else:
                self._con.execute(
                    f"INSERT OR IGNORE INTO {item.table} ({cs}) VALUES ({ph})", vals)'''

# --- site 2: _Writer._heartbeat ---------------------------------------------
OLD_HB = '''            self._con.execute(
                "INSERT INTO service_health (service,status,last_heartbeat,meta) "
                "VALUES ('write_service',?,?,?) "
                "ON CONFLICT(service) DO UPDATE SET "
                "status=excluded.status, last_heartbeat=excluded.last_heartbeat, "
                "meta=excluded.meta",
                [status, datetime.now(timezone.utc).isoformat(),
                 json.dumps({"port": PORT, "db": str(self.db_path), "pid": os.getpid()})]
            )'''

NEW_HB = '''            _meta = json.dumps({"port": PORT, "db": str(self.db_path), "pid": os.getpid()})
            _now = datetime.now(timezone.utc).isoformat()
            if self._con.execute(
                    "SELECT 1 FROM service_health WHERE service='write_service' LIMIT 1"
                    ).fetchone():
                self._con.execute(
                    "UPDATE service_health SET status=?, last_heartbeat=?, meta=? "
                    "WHERE service='write_service'", [status, _now, _meta])
            else:
                self._con.execute(
                    "INSERT INTO service_health (service,status,last_heartbeat,meta) "
                    "VALUES ('write_service',?,?,?)", [status, _now, _meta])'''

PATCHES = [("_write upsert", OLD_WRITE, NEW_WRITE), ("_heartbeat", OLD_HB, NEW_HB)]


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

    if "ON CONFLICT" not in src:
        print("No ON CONFLICT found -- already patched or different version. No change.")
        return 0

    out, applied = src, []
    for name, old, new in PATCHES:
        if old in out:
            out = out.replace(old, new, 1)
            applied.append(name)
        else:
            print(f"WARN: '{name}' block not found verbatim -- skipped (version drift?)")

    if not applied:
        print("Nothing matched -- no change. Inspect the file manually.")
        return 1
    if "ON CONFLICT" in out:
        print("WARN: ON CONFLICT still present after patch -- review manually:")
        for i, line in enumerate(out.splitlines(), 1):
            if "ON CONFLICT" in line:
                print(f"  {i}: {line.strip()}")

    if args.dry_run:
        print(f"[dry-run] would patch {applied} in {path}")
        return 0

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(out, encoding="utf-8")
    print(f"Patched {applied} in {path} (backup: {backup})")
    print("Now run:  pkill -f write_service.py   (wrapper relaunches the patched one)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
