import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "perspective_query_api_router"


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        raise


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed: {e}")
        raise


def ws_execute(sql: str) -> None:
    try:
        resp = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_execute failed: {e}")
        raise


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


router = APIRouter(prefix="/api/perspectives", tags=["perspectives"])


def ensure_perspective_snapshots_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS perspective_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        perspective_name VARCHAR,
        signal_type VARCHAR,
        signal_score DOUBLE,
        evidence_blob TEXT,
        computed_at TIMESTAMPTZ,
        metadata JSON
    )
    """
    ws_execute(sql)


def get_perspective_by_snapshot(snapshot_id: str) -> Optional[Dict[str, Any]]:
    sql = f"SELECT * FROM perspective_snapshots WHERE snapshot_id = '{snapshot_id}'"
    rows = ws_query(sql)
    return rows[0] if rows else None


def get_perspectives_for_server(server_id: str) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM perspective_snapshots WHERE server_id = '{server_id}' ORDER BY computed_at DESC"
    return ws_query(sql)


def get_perspectives_by_type(
    perspective_name: str, limit: int = 100
) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM perspective_snapshots WHERE perspective_name = '{perspective_name}' ORDER BY computed_at DESC LIMIT {limit}"
    return ws_query(sql)


def get_recent_perspectives(limit: int = 50) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM perspective_snapshots ORDER BY computed_at DESC LIMIT {limit}"
    return ws_query(sql)


def get_perspective_signal_summary(server_id: str) -> Dict[str, Any]:
    sql = f"""
    SELECT 
        perspective_name,
        signal_type,
        AVG(signal_score) as avg_score,
        COUNT(*) as count,
        MAX(computed_at) as last_computed
    FROM perspective_snapshots 
    WHERE server_id = '{server_id}'
    GROUP BY perspective_name, signal_type
    """
    return ws_query(sql)


def get_perspective_trends(
    server_id: str, perspective_name: str, days: int = 30
) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        DATE(computed_at) as date,
        AVG(signal_score) as avg_score,
        COUNT(*) as sample_count
    FROM perspective_snapshots 
    WHERE server_id = '{server_id}' 
      AND perspective_name = '{perspective_name}'
      AND computed_at >= NOW() - INTERVAL '{days} days'
    GROUP BY DATE(computed_at)
    ORDER BY date
    """
    return ws_query(sql)


def compute_perspective_id(server_id: str, perspective_name: str, computed_at: str) -> str:
    import hashlib
    raw = f"{server_id}:{perspective_name}:{computed_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


@router.get("/snapshot/{snapshot_id}")
def get_snapshot(snapshot_id: str) -> Dict[str, Any]:
    snapshot = get_perspective_by_snapshot(snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")
    return snapshot


@router.get("/server/{server_id}")
def list_server_perspectives(server_id: str) -> Dict[str, Any]:
    perspectives = get_perspectives_for_server(server_id)
    summary = get_perspective_signal_summary(server_id)
    return {
        "server_id": server_id,
        "count": len(perspectives),
        "perspectives": perspectives,
        "summary": summary,
    }


@router.get("/type/{perspective_name}")
def list_by_type(
    perspective_name: str, limit: int = Query(default=100, le=500)
) -> Dict[str, Any]:
    perspectives = get_perspectives_by_type(perspective_name, limit=limit)
    return {"perspective_name": perspective_name, "count": len(perspectives), "items": perspectives}


@router.get("/recent")
def list_recent(limit: int = Query(default=50, le=200)) -> Dict[str, Any]:
    perspectives = get_recent_perspectives(limit=limit)
    return {"count": len(perspectives), "items": perspectives}


@router.get("/server/{server_id}/summary")
def server_signal_summary(server_id: str) -> Dict[str, Any]:
    summary = get_perspective_signal_summary(server_id)
    return {"server_id": server_id, "summary": summary}


@router.get("/server/{server_id}/perspective/{perspective_name}/trends")
def perspective_trends(
    server_id: str,
    perspective_name: str,
    days: int = Query(default=30, ge=1, le=365),
) -> Dict[str, Any]:
    trends = get_perspective_trends(server_id, perspective_name, days=days)
    return {
        "server_id": server_id,
        "perspective_name": perspective_name,
        "days": days,
        "trends": trends,
    }


@router.post("/snapshot")
def create_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot_id = payload.get("snapshot_id") or compute_perspective_id(
        payload.get("server_id", ""),
        payload.get("perspective_name", ""),
        utc_now_iso(),
    )
    row = {
        "snapshot_id": snapshot_id,
        "server_id": payload.get("server_id"),
        "perspective_name": payload.get("perspective_name"),
        "signal_type": payload.get("signal_type"),
        "signal_score": payload.get("signal_score", 0.0),
        "evidence_blob": str(payload.get("evidence_blob", "")),
        "computed_at": payload.get("computed_at") or utc_now_iso(),
        "metadata": payload.get("metadata", {}),
    }
    ws_write("perspective_snapshots", [row])
    return {"created": snapshot_id, "row": row}


@router.delete("/snapshot/{snapshot_id}")
def delete_snapshot(snapshot_id: str) -> Dict[str, Any]:
    sql = f"DELETE FROM perspective_snapshots WHERE snapshot_id = '{snapshot_id}'"
    ws_execute(sql)
    return {"deleted": snapshot_id}


@router.get("/stats/overview")
def perspective_stats() -> Dict[str, Any]:
    total_sql = "SELECT COUNT(*) as total FROM perspective_snapshots"
    total_rows = ws_query(total_sql)
    total = total_rows[0]["total"] if total_rows else 0

    by_type_sql = """
    SELECT perspective_name, COUNT(*) as count 
    FROM perspective_snapshots 
    GROUP BY perspective_name
    """
    by_type = ws_query(by_type_sql)

    by_signal_sql = """
    SELECT signal_type, COUNT(*) as count, AVG(signal_score) as avg_score
    FROM perspective_snapshots 
    GROUP BY signal_type
    """
    by_signal = ws_query(by_signal_sql)

    return {
        "total_snapshots": total,
        "by_perspective": by_type,
        "by_signal_type": by_signal,
        "generated_at": utc_now_iso(),
    }


if __name__ == "__main__":
    ensure_perspective_snapshots_table()
    logger.info(f"{SERVICE_NAME} initialized")
    sys.exit(0)