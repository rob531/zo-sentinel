#!/usr/bin/env python3
"""Capture the live write-service catalog into a committed snapshot.

WHY THIS EXISTS
    GitHub Actions cannot reach the live bus (127.0.0.1:8772). Referent
    verification in CI therefore checks code against this snapshot rather than
    against the bus directly. This script is the only thing that writes it, and
    it runs on the ZoComputer host where the bus is reachable.

    It renders no verdict. It cannot fail a build. Its single responsibility is
    that schema/bus_catalog.json describes the bus as it actually is.

THE TRUNCATION RULE (FU/G6)
    :8772 caps result sets at 200 rows and -- as measured 2026-08-26 -- does NOT
    set a `truncated` flag when it does. The live catalog holds 355 columns, so
    an unpaginated read silently returns 56% of it.

    A snapshot built from a truncated read is worse than no snapshot: every
    missing column becomes a false MISSING verdict downstream, and false
    failures are how gates get switched off. So this script paginates, and then
    asserts the row count it assembled equals the COUNT(*) the bus reports.
    On mismatch it writes NOTHING and exits non-zero. A partial snapshot is
    never published.

Usage:
    python tools/bus_catalog_snapshot.py --emit schema/bus_catalog.json
    python tools/bus_catalog_snapshot.py --check          # drift, no write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BUS = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
PAGE = 150          # deliberately below the 200-row cap
TIMEOUT = 30
DEFAULT_OUT = Path("schema/bus_catalog.json")


class BusError(RuntimeError):
    """The bus could not be read. Never silently degraded into an empty result."""


def _query(sql: str) -> list[dict]:
    try:
        r = requests.post(f"{BUS}/query", json={"sql": sql}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("rows", []) or []
    except Exception as exc:                      # noqa: BLE001 -- re-raised as BusError
        raise BusError(f"{type(exc).__name__} querying bus") from None


def _scalar_count(table_expr: str) -> int:
    rows = _query(f"SELECT count(*) AS n FROM {table_expr}")
    if not rows:
        raise BusError(f"count query returned no rows for {table_expr}")
    return int(rows[0]["n"])


def _paginated(select: str, from_expr: str, order: str) -> list[dict]:
    """Read every row of `from_expr`, then prove none were dropped.

    The proof is the point. Without it this function cannot tell 355 rows from
    the first 200 of 355, and neither can anything downstream.
    """
    expected = _scalar_count(from_expr)
    out: list[dict] = []
    offset = 0
    while True:
        page = _query(
            f"SELECT {select} FROM {from_expr} ORDER BY {order} "
            f"LIMIT {PAGE} OFFSET {offset}"
        )
        out.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
        if offset > 200_000:                      # runaway guard
            raise BusError("pagination exceeded 200k rows; refusing to continue")

    if len(out) != expected:
        raise BusError(
            f"pagination mismatch on {from_expr}: assembled {len(out)}, "
            f"bus reports {expected}. Refusing to write a partial snapshot."
        )
    return out


def capture() -> dict:
    tables = _paginated("table_name", "information_schema.tables", "table_name")
    columns = _paginated(
        "table_name, column_name, data_type",
        "information_schema.columns",
        "table_name, column_name",
    )

    by_table: dict[str, dict[str, str]] = {t["table_name"]: {} for t in tables}
    orphan_cols = 0
    for c in columns:
        tn = c["table_name"]
        if tn not in by_table:                    # column whose table is not listed
            by_table[tn] = {}
            orphan_cols += 1
        by_table[tn][c["column_name"]] = c.get("data_type") or "unknown"

    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "write_service:information_schema",
        "table_count": len(by_table),
        "column_count": sum(len(v) for v in by_table.values()),
        "columns_with_unlisted_table": orphan_cols,
        "tables": {k: dict(sorted(v.items())) for k, v in sorted(by_table.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit", type=Path, nargs="?", const=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true",
                    help="compare live bus to the committed snapshot; write nothing")
    args = ap.parse_args()

    try:
        snap = capture()
    except BusError as exc:
        # Loud and non-zero. The caller must be able to tell "bus unreachable"
        # from "bus is empty" -- conflating them is the bug this repo keeps
        # re-fixing.
        print(f"[bus-catalog] UNREACHABLE: {exc}", file=sys.stderr)
        return 2

    print(f"[bus-catalog] {snap['table_count']} tables, "
          f"{snap['column_count']} columns (paginated, reconciled)")

    if args.check:
        out = DEFAULT_OUT
        if not out.exists():
            print(f"[bus-catalog] DRIFT: {out} does not exist")
            return 1
        old = json.loads(out.read_text())
        added = sorted(set(snap["tables"]) - set(old.get("tables", {})))
        removed = sorted(set(old.get("tables", {})) - set(snap["tables"]))
        if added or removed:
            print(f"[bus-catalog] DRIFT: +{len(added)} -{len(removed)} tables")
            for t in added:
                print(f"    + {t}")
            for t in removed:
                print(f"    - {t}")
            return 1
        print("[bus-catalog] snapshot matches live bus")
        return 0

    if args.emit:
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        args.emit.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
        print(f"[bus-catalog] wrote {args.emit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
