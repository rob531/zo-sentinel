#!/usr/bin/env python3
"""
duckdb_schema_uptime_probe.py -- DuckDB liveness + schema-drift probe.

Two responsibilities in one tight loop (cheaper than two daemons sharing
identical request/state plumbing):

1. **Uptime monitoring.** Every cycle, hit write_service `/query` with
   ``SELECT 1``. Emit a mesh_memory row on state transitions (ok->down,
   down->ok) and a liveness pulse every N cycles. We deliberately do NOT
   emit a row every cycle -- that would flood mesh_memory with
   uninteresting heartbeats and drown the genuinely-actionable outage
   signal.

2. **Schema drift detection.** On every successful uptime check,
   re-query ``information_schema.columns`` (same query
   ``refresh_schema_doc.py`` uses), compute the canonical schema hash,
   and compare against the committed ``schemas/duckdb_schema.json``.
   On mismatch, write the new schema to
   ``schemas/.pending_drift/duckdb_schema.json`` (NOT overwriting the
   canonical file) and emit a high-importance mesh_memory row so the
   Directive Architect picks it up. Promotion to canonical stays a
   manual gesture; the probe never auto-commits.

Same cycle: introspect ``mesh_memory.db`` via ``PRAGMA table_info``,
hash, compare to ``mesh_memory_schema.json``, stage drift the same way.

OPERATION
---------

CLI::

    python3 -m zo_sentinel.probes.duckdb_schema_uptime_probe --once
    python3 -m zo_sentinel.probes.duckdb_schema_uptime_probe --interval 300

* `--once`: run one cycle and exit (manual run, CI smoke test).
* No `--once`: daemon mode, sleeps ``--interval`` seconds between cycles.

Dependencies: stdlib + ``requests`` (already a tower dep per
``refresh_schema_doc.py``).

Logs to ``/home/workspace/logs/duckdb_schema_uptime_probe.log`` if that
directory exists & is writable; otherwise to stderr.

**Dormant until supervisord registration.** Same Phase 0b pattern --
ships without a supervisord block. A suggested entry is in
``docs/SCHEMA_AS_CODE.md``.

Exit codes (one-shot mode):
  0  cycle completed (uptime ok, no drift) OR drift detected & staged
  1  cycle failed in an unexpected way
  2  config error (write_service URL unreachable to even probe)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# requests is already a tower dep (refresh_schema_doc.py imports it).
import requests

# Loader lives in the same repo; import via package path so this script
# works whether invoked as `python -m zo_sentinel.probes.duckdb_schema_uptime_probe`
# or via supervisord's full module path.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path scaffolding
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.schemas.loader import (  # noqa: E402
    compute_schema_hash,
    load_duckdb_schema,
    load_mesh_memory_schema,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SERVICE_NAME = "duckdb_schema_uptime_probe"
AGENT_ID = SERVICE_NAME

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 10.0
WRITE_TIMEOUT = 8.0

MESH_MEMORY_DB = Path("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
LOG_DIR = Path("/home/workspace/logs")
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

# Heartbeat pulse cadence: 1 row every N successful cycles. At 5min cadence
# (the default --interval), 12 cycles = ~1 hour.
LIVENESS_PULSE_EVERY = 12

INFORMATION_SCHEMA_SQL = (
    "SELECT table_name, column_name, data_type "
    "FROM information_schema.columns WHERE table_schema='main' "
    "ORDER BY table_name, ordinal_position"
)

# The bus (:8772/query) silently caps a result set. Measured on the LIVE runtime
# 2026-09-04: the unpaginated statement above returned exactly 200 rows while
# `count(*)` over the same predicate returned 373 -- so this drift probe was
# blind to 173 of 373 columns, every column of every table sorting after
# `mcp_ecosystems_metadata`. It never reported drift there, and a probe that
# cannot see a region does not report "unknown" for it; it reports "no drift".
#
# The response DOES carry a `count` field, but it equals len(rows) on every
# query (7 for a 7-row read, 200 for the truncated read), so it cannot
# distinguish "capped at 200" from "there were exactly 200". That is the gap
# #3997 exists to close. Paging does NOT need it: ask for pages and stop when
# one comes back short. `(table_name, ordinal_position)` is a total order, so
# LIMIT/OFFSET is stable across pages.
BUS_PAGE_ROWS = 200
BUS_MAX_PAGES = 200  # 40k columns; a runaway loop is a bug, not a big schema

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

PENDING_DRIFT_DIR = REPO_ROOT / "schemas" / ".pending_drift"
CANONICAL_DUCKDB_PATH = REPO_ROOT / "schemas" / "duckdb_schema.json"
CANONICAL_MESH_PATH = REPO_ROOT / "schemas" / "mesh_memory_schema.json"


# ---------------------------------------------------------------------------
# Logging (single FileHandler per builder_conventions.json
# "daemon_logging_filehandler_only"; falls back to stderr if /home/workspace
# isn't available, e.g. on Windows / CI)
# ---------------------------------------------------------------------------


def _configure_logging() -> logging.Logger:
    log = logging.getLogger(SERVICE_NAME)
    if log.handlers:  # pragma: no cover - reconfigure guard
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    handler: logging.Handler
    try:
        if LOG_DIR.is_dir() and os.access(LOG_DIR, os.W_OK):
            handler = logging.FileHandler(str(LOG_FILE))
        else:
            handler = logging.StreamHandler(sys.stderr)
    except Exception:  # pragma: no cover - filesystem oddity
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return log


logger = _configure_logging()


# ---------------------------------------------------------------------------
# Uptime state machine
# ---------------------------------------------------------------------------


class UptimeState:
    """In-memory state machine. Survives a single ``run()`` invocation; we
    deliberately don't persist across restarts (a restart is itself a state
    transition worth re-emitting)."""

    def __init__(self) -> None:
        self.last_status: Optional[str] = None  # "ok" | "down"
        self.consecutive_failures: int = 0
        self.cycles_since_pulse: int = 0

    def observe(self, ok: bool) -> Tuple[bool, str]:
        """Update state, return (should_emit, reason)."""
        new_status = "ok" if ok else "down"
        if not ok:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0

        # State transition: always emit
        if self.last_status is None or self.last_status != new_status:
            old = self.last_status
            self.last_status = new_status
            self.cycles_since_pulse = 0
            reason = (
                "first_observation"
                if old is None
                else f"transition_{old}_to_{new_status}"
            )
            return True, reason

        # Same status as before
        if ok:
            self.cycles_since_pulse += 1
            if self.cycles_since_pulse >= LIVENESS_PULSE_EVERY:
                self.cycles_since_pulse = 0
                return True, "liveness_pulse"
            return False, "no_change"
        # Down -> down: don't spam; the original transition already fired.
        return False, "still_down"


# ---------------------------------------------------------------------------
# mesh_memory write path
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_mesh_memory(
    memory_type: str,
    content: Dict[str, Any],
    importance: float,
    *,
    dry_run: bool = False,
) -> bool:
    """Write a row to mesh_memory. Tries write_service first, falls back to
    direct sqlite3 (mesh_memory is SQLite, per builder_conventions.json
    'sqlite_mesh_memory_is_separate'). Returns True on success."""
    row = {
        "agent_id": AGENT_ID,
        "memory_type": memory_type,
        "content": json.dumps(content, sort_keys=True),
        "importance": importance,
        "created_at": _utc_iso(),
    }
    if dry_run:
        logger.info("DRY-RUN would emit mesh_memory row: %s", row)
        return True
    # Try write_service
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "mesh_memory", "rows": [row], "wait": True},
            timeout=WRITE_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("write_service mesh_memory write failed: %s", e)
    # Fallback: direct sqlite3
    if not MESH_MEMORY_DB.exists():
        logger.error(
            "mesh_memory sqlite fallback path %s does not exist; row dropped",
            MESH_MEMORY_DB,
        )
        return False
    try:
        conn = sqlite3.connect(str(MESH_MEMORY_DB))
        try:
            conn.execute(
                "INSERT INTO mesh_memory (agent_id, memory_type, content, importance, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row["agent_id"],
                    row["memory_type"],
                    row["content"],
                    row["importance"],
                    row["created_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        logger.error("sqlite fallback failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Uptime check
# ---------------------------------------------------------------------------


def check_uptime() -> Tuple[bool, float, Optional[str]]:
    """Return (ok, latency_ms, error_msg_or_none)."""
    t0 = time.monotonic()
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1 AS ok"},
            timeout=QUERY_TIMEOUT,
        )
        r.raise_for_status()
        latency_ms = (time.monotonic() - t0) * 1000.0
        return True, latency_ms, None
    except Exception as e:
        latency_ms = (time.monotonic() - t0) * 1000.0
        return False, latency_ms, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Schema drift detection -- DuckDB
# ---------------------------------------------------------------------------


def _fetch_all_information_schema_rows() -> List[Dict[str, Any]]:
    """Read every information_schema row, paging past the bus row cap.

    Stops on the first short page. A page that comes back exactly
    ``BUS_PAGE_ROWS`` long is indistinguishable from a capped one, so it is
    always followed by another request -- the extra round trip is the price of
    not being able to ask the bus whether it truncated.
    """
    rows: List[Dict[str, Any]] = []
    for page in range(BUS_MAX_PAGES):
        offset = page * BUS_PAGE_ROWS
        r = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "sql": (
                    f"{INFORMATION_SCHEMA_SQL} "
                    f"LIMIT {BUS_PAGE_ROWS} OFFSET {offset}"
                )
            },
            timeout=QUERY_TIMEOUT,
        )
        r.raise_for_status()
        page_rows = r.json().get("rows") or []
        rows.extend(page_rows)
        if len(page_rows) < BUS_PAGE_ROWS:
            return rows
    raise RuntimeError(
        "information_schema paging exceeded %d pages (%d rows) -- refusing to "
        "loop; the bus is likely ignoring OFFSET"
        % (BUS_MAX_PAGES, len(rows))
    )


def fetch_live_duckdb_columns() -> List[Dict[str, str]]:
    """Query the live information_schema. Returns the list of column rows
    suitable for ``compute_schema_hash``."""
    rows = _fetch_all_information_schema_rows()
    # Apply the same SKIP_TABLES filter the refresh script uses, so hashes
    # are comparable.
    return [
        {
            "table_name": row["table_name"],
            "column_name": row["column_name"],
            "data_type": row["data_type"].replace(" WITH TIME ZONE", "TZ"),
        }
        for row in rows
        if row["table_name"] not in SKIP_TABLES
    ]


def diff_schemas(
    canonical_tables: Dict[str, List[Dict[str, Any]]],
    live_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Compute a structured diff between canonical and live schemas.

    Returns ``{added_tables, removed_tables, modified_columns}`` where
    ``modified_columns`` is a list of ``{table, column, canonical_type, live_type, change}``.
    """
    live_tables: Dict[str, Dict[str, str]] = {}
    for r in live_rows:
        live_tables.setdefault(r["table_name"], {})[r["column_name"]] = r["data_type"]
    canonical: Dict[str, Dict[str, str]] = {
        t: {c["name"]: c["type"] for c in cols}
        for t, cols in canonical_tables.items()
    }
    added = sorted(set(live_tables) - set(canonical))
    removed = sorted(set(canonical) - set(live_tables))
    modified: List[Dict[str, Any]] = []
    for t in sorted(set(canonical) & set(live_tables)):
        cc, lc = canonical[t], live_tables[t]
        for col in sorted(set(lc) - set(cc)):
            modified.append(
                {
                    "table": t,
                    "column": col,
                    "canonical_type": None,
                    "live_type": lc[col],
                    "change": "added",
                }
            )
        for col in sorted(set(cc) - set(lc)):
            modified.append(
                {
                    "table": t,
                    "column": col,
                    "canonical_type": cc[col],
                    "live_type": None,
                    "change": "removed",
                }
            )
        for col in sorted(set(cc) & set(lc)):
            if cc[col] != lc[col]:
                modified.append(
                    {
                        "table": t,
                        "column": col,
                        "canonical_type": cc[col],
                        "live_type": lc[col],
                        "change": "retyped",
                    }
                )
    return {
        "added_tables": added,
        "removed_tables": removed,
        "modified_columns": modified,
    }


def _stage_pending_duckdb(live_rows: List[Dict[str, str]], live_hash: str) -> Path:
    PENDING_DRIFT_DIR.mkdir(parents=True, exist_ok=True)
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for r in live_rows:
        tables.setdefault(r["table_name"], []).append(
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "ordinal": len(tables[r["table_name"]]) + 1,
                "nullable": True,
            }
        )
    doc = {
        "generated_at": _utc_iso(),
        "engine": "duckdb",
        "source": (
            "live snapshot staged by duckdb_schema_uptime_probe; "
            "review and promote to schemas/duckdb_schema.json manually"
        ),
        "schema_hash": live_hash,
        "skipped_tables": sorted(SKIP_TABLES),
        "tables": tables,
    }
    out = PENDING_DRIFT_DIR / "duckdb_schema.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return out


def check_duckdb_drift(dry_run: bool = False) -> Dict[str, Any]:
    """Compare live DuckDB schema vs canonical. Stage diff to .pending_drift
    if mismatch. Returns a status dict for logging."""
    canonical = load_duckdb_schema()
    live_rows = fetch_live_duckdb_columns()
    live_hash = compute_schema_hash(live_rows)
    if live_hash == canonical.schema_hash:
        return {"drift": False, "live_hash": live_hash}
    canonical_tables_dict = {
        t: [
            {"name": c.name, "type": c.type, "ordinal": c.ordinal, "nullable": c.nullable}
            for c in canonical.columns(t)
        ]
        for t in canonical.tables()
    }
    diff = diff_schemas(canonical_tables_dict, live_rows)
    pending_path = (
        _stage_pending_duckdb(live_rows, live_hash) if not dry_run else None
    )
    return {
        "drift": True,
        "canonical_hash": canonical.schema_hash,
        "live_hash": live_hash,
        "pending_path": str(pending_path) if pending_path else None,
        "added_tables": diff["added_tables"],
        "removed_tables": diff["removed_tables"],
        "modified_columns": diff["modified_columns"],
    }


# ---------------------------------------------------------------------------
# Schema drift detection -- mesh_memory (SQLite)
# ---------------------------------------------------------------------------


def fetch_live_mesh_memory_columns() -> List[Dict[str, str]]:
    if not MESH_MEMORY_DB.exists():
        raise FileNotFoundError(f"mesh_memory db not found: {MESH_MEMORY_DB}")
    conn = sqlite3.connect(str(MESH_MEMORY_DB))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows: List[Dict[str, str]] = []
        for (tname,) in cur.fetchall():
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
        return rows
    finally:
        conn.close()


def _stage_pending_mesh(live_rows: List[Dict[str, str]], live_hash: str) -> Path:
    PENDING_DRIFT_DIR.mkdir(parents=True, exist_ok=True)
    tables: Dict[str, List[Dict[str, Any]]] = {}
    for r in live_rows:
        tables.setdefault(r["table_name"], []).append(
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "ordinal": len(tables[r["table_name"]]) + 1,
                "nullable": True,
            }
        )
    doc = {
        "generated_at": _utc_iso(),
        "engine": "sqlite",
        "source": (
            "live snapshot staged by duckdb_schema_uptime_probe; "
            "review and promote to schemas/mesh_memory_schema.json manually"
        ),
        "schema_hash": live_hash,
        "tables": tables,
    }
    out = PENDING_DRIFT_DIR / "mesh_memory_schema.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return out


def check_mesh_memory_drift(dry_run: bool = False) -> Dict[str, Any]:
    canonical = load_mesh_memory_schema()
    live_rows = fetch_live_mesh_memory_columns()
    live_hash = compute_schema_hash(live_rows)
    if live_hash == canonical.schema_hash:
        return {"drift": False, "live_hash": live_hash}
    pending_path = (
        _stage_pending_mesh(live_rows, live_hash) if not dry_run else None
    )
    return {
        "drift": True,
        "canonical_hash": canonical.schema_hash,
        "live_hash": live_hash,
        "pending_path": str(pending_path) if pending_path else None,
    }


# ---------------------------------------------------------------------------
# Cycle orchestration
# ---------------------------------------------------------------------------


def cycle(
    state: UptimeState,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """One full uptime + drift cycle. Returns a structured summary."""
    summary: Dict[str, Any] = {"checked_at": _utc_iso()}

    # --- Uptime ---
    ok, latency_ms, err = check_uptime()
    should_emit, reason = state.observe(ok)
    summary["uptime"] = {
        "ok": ok,
        "latency_ms": round(latency_ms, 2),
        "error": err,
        "emit_reason": reason,
        "emitted": False,
    }
    if should_emit:
        if ok:
            mtype = "duckdb_uptime_ok"
            importance = 0.3
            content = {
                "latency_ms": round(latency_ms, 2),
                "checked_at": summary["checked_at"],
                "reason": reason,
            }
        else:
            mtype = "duckdb_outage"
            importance = 0.9
            content = {
                "error": err,
                "url": f"{WRITE_SERVICE_URL}/query",
                "consecutive_failures": state.consecutive_failures,
                "checked_at": summary["checked_at"],
                "reason": reason,
            }
        summary["uptime"]["emitted"] = _emit_mesh_memory(
            mtype, content, importance, dry_run=dry_run
        )

    if not ok:
        # Skip drift checks when the DB is unreachable.
        logger.info("cycle summary: %s", json.dumps(summary))
        return summary

    # --- DuckDB drift ---
    try:
        duck_drift = check_duckdb_drift(dry_run=dry_run)
    except Exception as e:
        duck_drift = {"drift": False, "error": f"{type(e).__name__}: {e}"}
        logger.error("duckdb drift check raised: %s", e)
    summary["duckdb_drift"] = duck_drift
    if duck_drift.get("drift"):
        _emit_mesh_memory(
            "duckdb_schema_drift",
            {
                "engine": "duckdb",
                "canonical_hash": duck_drift.get("canonical_hash"),
                "live_hash": duck_drift.get("live_hash"),
                "added_tables": duck_drift.get("added_tables", []),
                "removed_tables": duck_drift.get("removed_tables", []),
                "modified_columns": duck_drift.get("modified_columns", []),
                "pending_path": duck_drift.get("pending_path"),
                "checked_at": summary["checked_at"],
            },
            0.8,
            dry_run=dry_run,
        )

    # --- mesh_memory drift ---
    try:
        mesh_drift = check_mesh_memory_drift(dry_run=dry_run)
    except FileNotFoundError as e:
        # Windows / CI hosts won't have mesh_memory.db; not an error here.
        mesh_drift = {"drift": False, "skipped": True, "reason": str(e)}
    except Exception as e:
        mesh_drift = {"drift": False, "error": f"{type(e).__name__}: {e}"}
        logger.error("mesh drift check raised: %s", e)
    summary["mesh_memory_drift"] = mesh_drift
    if mesh_drift.get("drift"):
        _emit_mesh_memory(
            "mesh_memory_schema_drift",
            {
                "engine": "sqlite",
                "canonical_hash": mesh_drift.get("canonical_hash"),
                "live_hash": mesh_drift.get("live_hash"),
                "pending_path": mesh_drift.get("pending_path"),
                "checked_at": summary["checked_at"],
            },
            0.8,
            dry_run=dry_run,
        )

    logger.info("cycle summary: %s", json.dumps(summary))
    return summary


_keep_running = True


def _signal_handler(signum, frame):  # pragma: no cover - signal path
    global _keep_running
    logger.info("received signal %s, shutting down after current cycle", signum)
    _keep_running = False


def run(interval: int, once: bool, dry_run: bool) -> int:
    state = UptimeState()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    if once:
        cycle(state, dry_run=dry_run)
        return 0
    while _keep_running:  # pragma: no cover - daemon loop
        cycle(state, dry_run=dry_run)
        for _ in range(interval):
            if not _keep_running:
                break
            time.sleep(1)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between cycles in daemon mode (default 300).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit (manual / CI mode).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a cycle but don't write mesh_memory rows or stage pending drift.",
    )
    args = parser.parse_args(argv)
    try:
        return run(interval=args.interval, once=args.once, dry_run=args.dry_run)
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    except Exception as e:
        logger.exception("fatal: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
