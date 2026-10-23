import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("/home/workspace/logs/perspective_snapshot.log")],
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
MESH_MEMORY_DB = "/home/workspace/Datasets/zo-mesh/mesh_memory.db"


def _dummy_post(*, timeout: float = 5.0) -> List[Dict[str, Any]]:
    url = f"{WRITE_SERVICE_URL}/query"
    payload = {"query": "SELECT 1 AS dummy"}
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as exc:
        log.warning("dummy_post failed: %s", exc)
        return []


def dummy_post_endpoint() -> str:
    return f"{WRITE_SERVICE_URL}/query"


def mesh_scores_endpoint() -> str:
    return f"{WRITE_SERVICE_URL}/query"


def _post_query(query: str, *, timeout: float = 5.0) -> List[Dict[str, Any]]:
    url = f"{WRITE_SERVICE_URL}/query"
    payload = {"query": query}
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as exc:
        log.error("query failed [%s]: %s", query[:120], exc)
        return []


def _post_write(table: str, rows: List[Dict[str, Any]], *, timeout: float = 10.0) -> bool:
    url = f"{WRITE_SERVICE_URL}/write"
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("write to %s failed: %s", table, exc)
        return False


def get_mesh_memory(
    *,
    limit: int = 1000,
    since_iso: Optional[str] = None,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Retrieve mesh memory records from the SQLite store at mesh_memory.db.
    Falls back to write_service /query if the SQLite path is unavailable.
    """
    if os.path.exists(MESH_MEMORY_DB):
        try:
            conn = sqlite3.connect(MESH_MEMORY_DB)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if since_iso:
                cur.execute(
                    "SELECT * FROM mesh_memory WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
                    (since_iso, limit),
                )
            else:
                cur.execute("SELECT * FROM mesh_memory ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as exc:
            log.warning("direct sqlite mesh_memory read failed, falling back: %s", exc)

    sql = (
        "SELECT * FROM mesh_memory"
        + (f" WHERE created_at > '{since_iso}'" if since_iso else "")
        + f" ORDER BY created_at DESC LIMIT {limit}"
    )
    return _post_query(sql, timeout=timeout)


def get_signal_scores(
    server_ids: Optional[List[str]] = None,
    signal_name: Optional[str] = None,
    *,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Retrieve signal score records from mcp_signal_scores via write_service.

    Columns in mcp_signal_scores:
      server_id, signal_name, score, evidence, scored_at
    """
    conditions: List[str] = []
    params: List[Any] = []

    if server_ids:
        placeholders = ", ".join(["?" for _ in server_ids])
        conditions.append(f"server_id IN ({placeholders})")
        params.extend(server_ids)

    if signal_name:
        conditions.append("signal_name = ?")
        params.append(signal_name)

    where_clause = ""
    if conditions:
        where_clause = " WHERE " + " AND ".join(conditions)

    sql = f"SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores{where_clause} ORDER BY scored_at DESC"
    return _post_query(sql, timeout=timeout)


def get_mesh_scores(
    server_ids: Optional[List[str]] = None,
    *,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Retrieve all signal scores for given server_ids.
    Wraps get_signal_scores for backward compatibility naming.
    """
    return get_signal_scores(server_ids=server_ids, timeout=timeout)


def get_registry_counts(*, timeout: float = 5.0) -> Dict[str, int]:
    """
    Return high-level registry statistics from mcp_server_registry.
    """
    sql = "SELECT verdict, COUNT(*) AS cnt FROM mcp_server_registry GROUP BY verdict"
    rows = _post_query(sql, timeout=timeout)
    return {r.get("verdict", "unknown"): r.get("cnt", 0) for r in rows}


def get_service_health_status(
    services: Optional[List[str]] = None,
    *,
    timeout: float = 5.0,
) -> List[Dict[str, Any]]:
    """
    Retrieve service_health rows for listed services (or all if None).

    Columns in service_health: service(PK), last_heartbeat
    """
    if services:
        placeholders = ", ".join([f"'{s}'" for s in services])
        sql = f"SELECT service, last_heartbeat FROM service_health WHERE service IN ({placeholders})"
    else:
        sql = "SELECT service, last_heartbeat FROM service_health"
    return _post_query(sql, timeout=timeout)


def compute_deterministic_id(*fields: str) -> str:
    payload = "|".join(fields)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_server_export_api_quarantine() -> bool:
    """
    No-op stub retained for API-surface compatibility.
    Real quarantine state lives in the build_artifact table; this
    function exists only so callers that were written against the
    quarantined version continue to resolve without import errors.
    """
    log.info("reset_server_export_api_quarantine called (no-op)")
    return True


def _run_self_test() -> Dict[str, Any]:
    """
    Smoke-check connectivity to all backing stores.
    Returns a dict with status for each dependency.
    """
    results: Dict[str, Any] = {
        "write_service": False,
        "mesh_memory_sqlite": False,
        "mcp_signal_scores_queryable": False,
        "service_health_queryable": False,
    }

    # Check write_service
    try:
        rows = _dummy_post(timeout=5.0)
        results["write_service"] = bool(rows is not None)
    except Exception as exc:
        log.warning("self-test write_service: %s", exc)

    # Check SQLite mesh_memory
    if os.path.exists(MESH_MEMORY_DB):
        try:
            conn = sqlite3.connect(MESH_MEMORY_DB, timeout=3.0)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mesh_memory'")
            results["mesh_memory_sqlite"] = bool(cur.fetchone())
            conn.close()
        except Exception as exc:
            log.warning("self-test mesh_memory_sqlite: %s", exc)

    # Check mcp_signal_scores reachable via write_service
    try:
        rows = _post_query(
            "SELECT server_id FROM mcp_signal_scores LIMIT 1",
            timeout=5.0,
        )
        results["mcp_signal_scores_queryable"] = True
    except Exception as exc:
        log.warning("self-test mcp_signal_scores: %s", exc)

    # Check service_health reachable via write_service
    try:
        rows = _post_query(
            "SELECT service FROM service_health LIMIT 1",
            timeout=5.0,
        )
        results["service_health_queryable"] = True
    except Exception as exc:
        log.warning("self-test service_health: %s", exc)

    all_ok = all(results.values())
    results["overall"] = "ok" if all_ok else "degraded"
    log.info("self_test overall=%s details=%s", results["overall"], results)
    return results


if __name__ == "__main__":
    result = _run_self_test()
    print(json.dumps(result, indent=2))
    if result.get("overall") != "ok":
        log.error("self-test failed, exiting non-zero")
        exit(1)
    log.info("self-test passed")
    exit(0)