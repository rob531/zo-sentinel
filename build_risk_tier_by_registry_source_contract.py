import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

SERVICE_NAME = "risk_tier_by_registry_source_api"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
WRITE_URL = "http://localhost:8772/write"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)

app = FastAPI(title="Risk Tier By Registry Source API", version="1.0.0")


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> None:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_event_id(registry_source: str, risk_tier: str) -> str:
    import hashlib
    raw = f"{registry_source}:{risk_tier}:{utc_now_iso()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/api/v1/risk-tier-by-registry-source")
def get_risk_tier_by_registry_source(
    min_trust_score: Optional[float] = Query(None, ge=0.0, le=100.0, description="Minimum trust score filter"),
    max_trust_score: Optional[float] = Query(None, ge=0.0, le=100.0, description="Maximum trust score filter"),
    registry_source_filter: Optional[str] = Query(None, description="Filter by specific registry source"),
    limit: int = Query(100, ge=1, le=10000, description="Max rows per source"),
) -> Dict[str, Any]:
    """
    Returns risk tier distribution broken down by registry_source.

    Returns a list of registry sources, each with:
      - registry_source: the source name (npm, github, smithery, etc.)
      - total_count: total servers from that source
      - risk_tiers: breakdown of counts per risk_tier
      - avg_trust_score: average trust score for that source
      - last_updated: ISO timestamp of computation
    """
    try:
        conditions: List[str] = []
        params: List[Any] = []

        if min_trust_score is not None:
            conditions.append("COALESCE(r.trust_score, 50.0) >= ?")
            params.append(min_trust_score)
        if max_trust_score is not None:
            conditions.append("COALESCE(r.trust_score, 50.0) <= ?")
            params.append(max_trust_score)
        if registry_source_filter:
            conditions.append("r.registry_source = ?")
            params.append(registry_source_filter)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        sql = f"""
        SELECT
            r.registry_source,
            r.risk_tier,
            COUNT(*) AS server_count,
            AVG(r.trust_score) AS avg_trust_score
        FROM mcp_server_registry r
        {where_clause}
        GROUP BY r.registry_source, r.risk_tier
        ORDER BY r.registry_source, r.risk_tier
        """

        rows = ws_query(sql, params)

        registry_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            src = row.get("registry_source") or "unknown"
            tier = row.get("risk_tier") or "UNKNOWN"
            if src not in registry_map:
                registry_map[src] = {
                    "registry_source": src,
                    "total_count": 0,
                    "risk_tiers": {},
                    "avg_trust_score": None,
                    "last_updated": utc_now_iso(),
                }
            registry_map[src]["risk_tiers"][tier] = row.get("server_count", 0)
            registry_map[src]["total_count"] += row.get("server_count", 0)

        summary_sql = f"""
        SELECT
            registry_source,
            SUM(server_count) AS total,
            AVG(avg_trust_score) AS overall_avg_score
        FROM (
            SELECT
                r.registry_source,
                r.risk_tier,
                COUNT(*) AS server_count,
                AVG(r.trust_score) AS avg_trust_score
            FROM mcp_server_registry r
            {where_clause}
            GROUP BY r.registry_source, r.risk_tier
        ) sub
        GROUP BY registry_source
        ORDER BY total DESC
        LIMIT ?
        """
        summary_params = params + [limit]
        summary_rows = ws_query(summary_sql, summary_params)

        for srow in summary_rows:
            src = srow.get("registry_source") or "unknown"
            if src in registry_map:
                registry_map[src]["avg_trust_score"] = round(srow.get("overall_avg_score") or 0.0, 2)
                registry_map[src]["total_count"] = srow.get("total", 0)

        results = list(registry_map.values())

        ws_write("audit_log", [{
            "event_id": compute_event_id("bulk", "risk_tier_by_registry"),
            "event_type": "api_call",
            "actor": "api",
            "action": "get_risk_tier_by_registry_source",
            "target_server_id": None,
            "details_json": f'{{"result_count": {len(results)}}}',
            "outcome": "success",
            "timestamp": utc_now_iso(),
            "immutable": False,
        }])

        return {
            "service": SERVICE_NAME,
            "ts": utc_now_iso(),
            "count": len(results),
            "filters": {
                "min_trust_score": min_trust_score,
                "max_trust_score": max_trust_score,
                "registry_source": registry_source_filter,
                "limit": limit,
            },
            "results": results,
        }

    except Exception as exc:
        log.exception("get_risk_tier_by_registry_source failed: %s", exc)
        ws_write("audit_log", [{
            "event_id": compute_event_id("error", "risk_tier_by_registry"),
            "event_type": "api_error",
            "actor": "api",
            "action": "get_risk_tier_by_registry_source",
            "target_server_id": None,
            "details_json": f'{{"error": "{str(exc)}"}}',
            "outcome": "failure",
            "timestamp": utc_now_iso(),
            "immutable": False,
        }])
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/risk-tier-by-registry-source/summary")
def get_summary() -> Dict[str, Any]:
    """
    Returns a high-level summary: total servers, unique sources, risk tier distribution across all sources.
    """
    try:
        total_sql = "SELECT COUNT(*) AS total FROM mcp_server_registry"
        sources_sql = "SELECT COUNT(DISTINCT registry_source) AS unique_sources FROM mcp_server_registry"
        tier_dist_sql = """
        SELECT
            risk_tier,
            COUNT(*) AS count
        FROM mcp_server_registry
        GROUP BY risk_tier
        ORDER BY count DESC
        """

        total_rows = ws_query(total_sql)
        sources_rows = ws_query(sources_sql)
        tier_rows = ws_query(tier_dist_sql)

        total = total_rows[0].get("total", 0) if total_rows else 0
        unique_sources = sources_rows[0].get("unique_sources", 0) if sources_rows else 0
        tier_distribution = {r.get("risk_tier") or "UNKNOWN": r.get("count", 0) for r in tier_rows}

        return {
            "service": SERVICE_NAME,
            "ts": utc_now_iso(),
            "total_servers": total,
            "unique_registry_sources": unique_sources,
            "risk_tier_distribution": tier_distribution,
        }

    except Exception as exc:
        log.exception("get_summary failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/risk-tier-by-registry-source/tier/{tier}")
def get_servers_by_tier_and_source(
    tier: str,
    registry_source: Optional[str] = Query(None, description="Filter by registry source"),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """
    Returns servers for a specific risk_tier, grouped by registry_source.
    """
    try:
        params: List[Any] = [tier]
        conditions = ["r.risk_tier = ?"]
        if registry_source:
            conditions.append("r.registry_source = ?")
            params.append(registry_source)

        where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
        SELECT
            r.server_id,
            r.name,
            r.registry_source,
            r.trust_score,
            r.verdict,
            r.risk_tier
        FROM mcp_server_registry r
        {where_clause}
        ORDER BY r.trust_score ASC NULLS LAST
        LIMIT ?
        """
        params.append(limit)
        rows = ws_query(sql, params)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            src = row.get("registry_source") or "unknown"
            if src not in grouped:
                grouped[src] = []
            grouped[src].append(row)

        return {
            "service": SERVICE_NAME,
            "ts": utc_now_iso(),
            "risk_tier": tier,
            "registry_source_filter": registry_source,
            "total_returned": len(rows),
            "grouped_by_source": grouped,
        }

    except Exception as exc:
        log.exception("get_servers_by_tier_and_source failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.exception_handler(Exception)
def global_exception_handler(request: Any, exc: Exception) -> JSONResponse:
    log.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc), "ts": utc_now_iso()},
    )


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()