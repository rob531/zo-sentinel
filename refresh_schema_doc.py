#!/usr/bin/env python3
"""
refresh_schema_doc.py
---------------------

Queries information_schema from the live DuckDB via write_service AND
introspects the SQLite mesh_memory.db, then writes three artefacts:

  1. DB_SCHEMA.md                  -- human-readable, unchanged shape
  2. schemas/duckdb_schema.json    -- machine-readable, with schema_hash
  3. schemas/mesh_memory_schema.json -- machine-readable, with schema_hash

Run any time tables change. Tower-side only -- DuckDB is loopback-bound.

  python3 refresh_schema_doc.py             # write
  python3 refresh_schema_doc.py --dry-run   # preview only

Falls back gracefully when mesh_memory.db isn't accessible (e.g. on
Windows / CI hosts) so the script can be run anywhere to refresh just
the DuckDB side.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 15.0

# Repo-root resolution: prefer __file__-relative so the script works from
# both the tower (`/home/workspace/zo_sentinel`) and a Windows checkout.
# Fall back to the historical hardcoded path for the existing cron entry.
HERE = Path(__file__).resolve()
REPO_ROOT_FROM_FILE = HERE.parent
FALLBACK_ROOT = Path("/home/workspace/zo_sentinel")


def _repo_root() -> Path:
    if (REPO_ROOT_FROM_FILE / "builder_conventions.json").exists():
        return REPO_ROOT_FROM_FILE
    if FALLBACK_ROOT.exists():  # pragma: no cover - tower-only fallback
        return FALLBACK_ROOT
    return REPO_ROOT_FROM_FILE


REPO_ROOT = _repo_root()
MD_OUT = REPO_ROOT / "DB_SCHEMA.md"
SCHEMAS_DIR = REPO_ROOT / "schemas"
DUCKDB_JSON = SCHEMAS_DIR / "duckdb_schema.json"
MESH_JSON = SCHEMAS_DIR / "mesh_memory_schema.json"

MESH_MEMORY_DB = Path(
    os.environ.get(
        "ZO_MESH_MEMORY_DB", "/home/workspace/Datasets/zo-mesh/mesh_memory.db"
    )
)

SKIP_TABLES = {
    "agent_outputs",
    "agent_runs",
    "corrections",
    "inference_log",
    "write_queue_log",
    "perf_metrics",
    "world_articles",
    "world_topics",
}

INFORMATION_SCHEMA_SQL = (
    "SELECT table_name, column_name, data_type "
    "FROM information_schema.columns WHERE table_schema='main' "
    "ORDER BY table_name, ordinal_position"
)

# The bus (:8772/query) silently caps a result set. Measured on the LIVE runtime
# 2026-09-04: this statement returned 200 rows against a `count(*)` of 373, so
# the committed DB_SCHEMA.md / duckdb_schema.json were generated from the first
# 200 columns only -- every table sorting after `mcp_ecosystems_metadata` was
# absent from the canonical doc, and the committed schema hash was a hash of a
# partial read. The response's `count` field equals len(rows) on every query
# and so cannot flag truncation (#3997). Paging does not need it: request pages
# and stop at the first short one. `(table_name, ordinal_position)` is a total
# order, so LIMIT/OFFSET is stable across pages.
BUS_PAGE_ROWS = 200
BUS_MAX_PAGES = 200


# ---------------------------------------------------------------------------
# Schema hash -- duplicated here so this script stays stdlib + requests only
# (matches zo_sentinel.schemas.loader.compute_schema_hash exactly)
# ---------------------------------------------------------------------------


def compute_schema_hash(rows: List[Dict[str, str]]) -> str:
    tuples = sorted(
        (r["table_name"], r["column_name"], r["data_type"]) for r in rows
    )
    h = hashlib.sha256()
    for t, c, dt in tuples:
        h.update(f"{t}|{c}|{dt}\n".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# DuckDB introspection
# ---------------------------------------------------------------------------


def _fetch_all_information_schema_rows() -> List[Dict[str, Any]]:
    """Read every information_schema row, paging past the bus row cap.

    Stops on the first short page. A full-length page is indistinguishable from
    a capped one, so it is always followed by another request.
    """
    rows: List[Dict[str, Any]] = []
    for page in range(BUS_MAX_PAGES):
        offset = page * BUS_PAGE_ROWS
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={
                "sql": (
                    f"{INFORMATION_SCHEMA_SQL} "
                    f"LIMIT {BUS_PAGE_ROWS} OFFSET {offset}"
                )
            },
            timeout=QUERY_TIMEOUT,
        )
        resp.raise_for_status()
        page_rows = resp.json().get("rows") or []
        rows.extend(page_rows)
        if len(page_rows) < BUS_PAGE_ROWS:
            return rows
    raise RuntimeError(
        "information_schema paging exceeded %d pages (%d rows) -- refusing to "
        "loop; the bus is likely ignoring OFFSET"
        % (BUS_MAX_PAGES, len(rows))
    )


def fetch_duckdb_rows() -> List[Dict[str, str]]:
    raw = _fetch_all_information_schema_rows()
    # Normalise type-string and drop skip tables -- match the historical
    # transformation so committed hashes stay comparable.
    return [
        {
            "table_name": r["table_name"],
            "column_name": r["column_name"],
            "data_type": r["data_type"].replace(" WITH TIME ZONE", "TZ"),
        }
        for r in raw
        if r["table_name"] not in SKIP_TABLES
    ]


def build_duckdb_doc(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    tables: Dict[str, List[Dict[str, Any]]] = {}
    ordinal_by_table: Dict[str, int] = {}
    for r in rows:
        t = r["table_name"]
        ordinal_by_table[t] = ordinal_by_table.get(t, 0) + 1
        tables.setdefault(t, []).append(
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "ordinal": ordinal_by_table[t],
                "nullable": True,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "duckdb",
        "source": f"{WRITE_SERVICE}/information_schema",
        "schema_hash": compute_schema_hash(rows),
        "skipped_tables": sorted(SKIP_TABLES),
        "tables": tables,
    }


def build_markdown(rows: List[Dict[str, str]]) -> str:
    tables: Dict[str, List[Tuple[str, str]]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], []).append(
            (r["column_name"], r["data_type"])
        )
    lines = [
        "# ZO-SENTINEL DuckDB Schema",
        f"# AUTO-GENERATED {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "# Regenerate: python3 /home/workspace/zo_sentinel/refresh_schema_doc.py",
        "",
    ]
    for table, cols in sorted(tables.items()):
        lines.append(f"## {table}")
        lines.append("| Column | Type |")
        lines.append("|--------|------|")
        for col, dtype in cols:
            lines.append(f"| {col} | {dtype} |")
        lines.append("")

    lines += [
        "## Common Mistakes",
        "- audit_log: `timestamp` not `created_at`; `target_server_id` not `server_id`",
        "- mcp_submissions: `requested_by` not `requester_name`; `mcp_name` not `mcp_identifier`",
        "- mcp_risk_register: `computed_at` not `last_assessed`",
        "- mcp_policy_rules: use `rule_type`+`pattern`, no condition_field/condition_operator",
        "- service_health: has `status` and `meta` columns",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# mesh_memory (SQLite) introspection
# ---------------------------------------------------------------------------


def fetch_mesh_memory_rows() -> List[Dict[str, str]]:
    if not MESH_MEMORY_DB.exists():
        raise FileNotFoundError(str(MESH_MEMORY_DB))
    rows: List[Dict[str, str]] = []
    conn = sqlite3.connect(str(MESH_MEMORY_DB))
    try:
        for (tname,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            for cid, cname, ctype, notnull, dflt, pk in conn.execute(
                f"PRAGMA table_info({tname})"
            ):
                rows.append(
                    {
                        "table_name": tname,
                        "column_name": cname,
                        "data_type": (ctype or "").upper() or "TEXT",
                    }
                )
    finally:
        conn.close()
    return rows


def build_mesh_doc(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    tables: Dict[str, List[Dict[str, Any]]] = {}
    ordinal_by_table: Dict[str, int] = {}
    for r in rows:
        t = r["table_name"]
        ordinal_by_table[t] = ordinal_by_table.get(t, 0) + 1
        tables.setdefault(t, []).append(
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "ordinal": ordinal_by_table[t],
                "nullable": True,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "sqlite",
        "source": str(MESH_MEMORY_DB),
        "schema_hash": compute_schema_hash(rows),
        "tables": tables,
    }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _write_json(path: Path, doc: Dict[str, Any], dry_run: bool) -> None:
    payload = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    if dry_run:
        print(f"[dry-run] would write {path} ({len(payload)} bytes, "
              f"{len(doc.get('tables', {}))} tables, "
              f"hash={doc.get('schema_hash', '')[:12]})")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    print(f"wrote {path} ({len(payload)} bytes, "
          f"{len(doc.get('tables', {}))} tables, "
          f"hash={doc.get('schema_hash', '')[:12]})")


def _write_text(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write {path} ({len(text)} bytes)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path} ({len(text)} bytes)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written, but don't touch disk.",
    )
    args = parser.parse_args(argv)

    # ----- DuckDB side -----
    try:
        duck_rows = fetch_duckdb_rows()
    except Exception as e:
        print(f"FATAL: DuckDB introspection failed: {e}", file=sys.stderr)
        return 1
    duck_doc = build_duckdb_doc(duck_rows)
    markdown = build_markdown(duck_rows)
    _write_text(MD_OUT, markdown, args.dry_run)
    _write_json(DUCKDB_JSON, duck_doc, args.dry_run)

    # ----- mesh_memory side (best-effort) -----
    if MESH_MEMORY_DB.exists():
        try:
            mesh_rows = fetch_mesh_memory_rows()
            mesh_doc = build_mesh_doc(mesh_rows)
            _write_json(MESH_JSON, mesh_doc, args.dry_run)
        except Exception as e:
            print(f"WARN: mesh_memory introspection failed: {e}", file=sys.stderr)
    else:
        print(
            f"INFO: {MESH_MEMORY_DB} not present on this host; "
            "skipping mesh_memory_schema.json refresh. "
            "Run on the tower to refresh it."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
