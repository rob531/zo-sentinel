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
log = logging.getLogger("verdict_view")

PROJECT_DIR = "/home/workspace/zo_sentinel"
sys.path.insert(0, PROJECT_DIR)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"

router = APIRouter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT via write_service query endpoint."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(
        f"{QUERY_SERVICE_URL}/query",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    """Write rows to a table via write_service write endpoint."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={"table": table, "rows": rows, "wait": True},
        timeout=10,
    )
    resp.raise_for_status()


def get_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all signal-score rows for a given server_id from mcp_signal_scores.

    Schema (verified against live information_schema):
      server_id, signal_name, score, evidence, scored_at
    """
    sql = """
        SELECT server_id, signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY signal_name
    """
    return ws_query(sql, {"p1": server_id})


def get_registry_record(server_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a single server row from mcp_server_registry.
    """
    sql = "SELECT * FROM mcp_server_registry WHERE server_id = ?"
    rows = ws_query(sql, {"p1": server_id})
    return rows[0] if rows else None


def get_verdict(server_id: str) -> Dict[str, Any]:
    """
    Return verdict decision data for a server: registry record + signal scores.

    Returns a dict with keys: server, signals, verdict, risk_tier, computed_at.
    """
    reg = get_registry_record(server_id)
    if not reg:
        raise HTTPException(status_code=404, detail=f"server_id not found: {server_id}")

    signals = get_signal_scores(server_id)
    return {
        "server": reg,
        "signals": signals,
        "verdict": reg.get("verdict"),
        "trust_score": reg.get("trust_score"),
        "risk_tier": reg.get("risk_tier"),
        "computed_at": utc_now_iso(),
    }


def list_servers(
    verdict: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """
    Paginated list of servers, optionally filtered by verdict.

    Schema (verified):
      mcp_server_registry: server_id, name, url, description, trust_score,
                            verdict, registry_source, scan_count
    """
    if verdict:
        sql = """
            SELECT server_id, name, url, verdict, trust_score, registry_source, scan_count
            FROM mcp_server_registry
            WHERE verdict = ?
            ORDER BY trust_score DESC NULLS LAST
            LIMIT ? OFFSET ?
        """
        rows = ws_query(sql, {"p1": verdict, "p2": limit, "p3": offset})
    else:
        sql = """
            SELECT server_id, name, url, verdict, trust_score, registry_source, scan_count
            FROM mcp_server_registry
            ORDER BY trust_score DESC NULLS LAST
            LIMIT ? OFFSET ?
        """
        rows = ws_query(sql, {"p1": limit, "p2": offset})

    count_sql = (
        "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE verdict = ?"
        if verdict
        else "SELECT COUNT(*) AS cnt FROM mcp_server_registry"
    )
    count_params = {"p1": verdict} if verdict else None
    count_rows = ws_query(count_sql, count_params)
    total = count_rows[0]["cnt"] if count_rows else 0

    return {"servers": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "verdict_view", "ts": utc_now_iso()}


@router.get("/server/{server_id}")
def get_server(server_id: str) -> Dict[str, Any]:
    return get_verdict(server_id)


@router.get("/servers")
def list_servers_endpoint(
    verdict: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    return list_servers(verdict=verdict, limit=limit, offset=offset)


if __name__ == "__main__":
    import uvicorn

    from fastapi import FastAPI

    app = FastAPI(title="verdict_view")
    app.include_router(router)

    log.info("Starting verdict_view service on port 0")
    uvicorn.run(app, host="0.0.0.0", port=0)