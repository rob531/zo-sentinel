#!/usr/bin/env python3
"""Cutover parity gate: assert the app-E2E snapshot is identical across the
DuckDB and Postgres backends (modulo the `backend` field). Fails closed so a
DuckDB->Postgres behavioural divergence cannot ship silently.

Usage: diff_app_e2e_snapshots.py <duckdb_snapshot.json> <postgres_snapshot.json>
"""
import difflib
import json
import sys
from pathlib import Path


def _load(p: str) -> dict:
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    d.pop("backend", None)  # the one field that is allowed to differ
    return d


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: diff_app_e2e_snapshots.py <duckdb.json> <postgres.json>")
        return 2
    a_path, b_path = sys.argv[1], sys.argv[2]
    a, b = _load(a_path), _load(b_path)
    if a == b:
        print("PARITY OK: duckdb and postgres app-E2E snapshots are identical (modulo backend).")
        return 0
    sa = json.dumps(a, indent=2, sort_keys=True).splitlines()
    sb = json.dumps(b, indent=2, sort_keys=True).splitlines()
    print("PARITY FAIL: app-E2E snapshots diverge across the DuckDB->Postgres cutover.\n")
    print("\n".join(difflib.unified_diff(sa, sb, fromfile=a_path, tofile=b_path, lineterm="")))
    return 1


if __name__ == "__main__":
    sys.exit(main())
