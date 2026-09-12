import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

SERVICE_NAME = "server_registry_source_distribution_router"
PORT = 8782
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"

LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["registry-source-distribution"])


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        log.error(f"ws_query failed for SQL: {sql[:200]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        response = requests.post(
            WRITE_URL,
            json={"table": table, "rows": rows},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_registry_source_distribution() -> Dict[str, Any]:
    sql = """
    SELECT
        registry_source,
        COUNT(*) as server_count,
        ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM mcp_server_registry), 2) as percentage,
        AVG(trust_score) as avg_trust_score,
        SUM(CASE WHEN verdict = 'TRUSTED' THEN 1 ELSE 0 END) as trusted_count,
        SUM(CASE WHEN verdict = 'AMBER' THEN 1 ELSE 0 END) as amber_count,
        SUM(CASE WHEN verdict = 'UNTRUSTED' THEN 1 ELSE 0 END) as untrusted_count,
        SUM(CASE WHEN verdict = 'UNKNOWN' THEN 1 ELSE 0 END) as unknown_count,
        SUM(CASE WHEN verdict = 'KNOWN_THREAT' THEN 1 ELSE 0 END) as known_threat_count
    FROM mcp_server_registry
    GROUP BY registry_source
    ORDER BY server_count DESC
    """
    return ws_query(sql)


def get_source_detail_by_verdict(source: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT
        verdict,
        COUNT(*) as count
    FROM mcp_server_registry
    WHERE registry_source = ?
    GROUP BY verdict
    ORDER BY count DESC
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": [source]},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        log.error(f"get_source_detail_by_verdict failed for source {source}: {e}")
        return []


def get_source_detail_by_risk_tier(source: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT
        r.risk_tier,
        COUNT(*) as count
    FROM mcp_server_registry r
    LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
    WHERE r.registry_source = ?
    GROUP BY r.risk_tier
    ORDER BY count DESC
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": [source]},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        log.error(f"get_source_detail_by_risk_tier failed for source {source}: {e}")
        return []


def get_top_sources_by_count(limit: int = 10) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT
        registry_source,
        COUNT(*) as server_count
    FROM mcp_server_registry
    GROUP BY registry_source
    ORDER BY server_count DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def get_registry_source_summary() -> Dict[str, Any]:
    total_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
    total_result = ws_query(total_sql)
    total_servers = total_result[0]["total"] if total_result else 0

    sources_sql = "SELECT COUNT(DISTINCT registry_source) as source_count FROM mcp_server_registry"
    sources_result = ws_query(sources_sql)
    source_count = sources_result[0]["source_count"] if sources_result else 0

    top_source_sql = """
    SELECT registry_source, COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY registry_source
    ORDER BY count DESC
    LIMIT 1
    """
    top_source_result = ws_query(top_source_sql)
    top_source = top_source_result[0]["registry_source"] if top_source_result else "N/A"

    return {
        "total_servers": total_servers,
        "unique_sources": source_count,
        "top_source": top_source,
        "generated_at": utc_now_iso(),
    }


@router.get("/registry/source-distribution")
def get_source_distribution() -> Dict[str, Any]:
    log.info("Fetching registry source distribution")
    distribution = get_registry_source_distribution()
    summary = get_registry_source_summary()
    return {
        "success": True,
        "summary": summary,
        "distribution": distribution,
    }


@router.get("/registry/source-distribution/summary")
def get_distribution_summary() -> Dict[str, Any]:
    log.info("Fetching registry source distribution summary")
    summary = get_registry_source_summary()
    return {"success": True, "data": summary}


@router.get("/registry/source-distribution/top")
def get_top_sources(limit: int = Query(default=10, ge=1, le=100)) -> Dict[str, Any]:
    log.info(f"Fetching top {limit} sources by server count")
    top_sources = get_top_sources_by_count(limit)
    return {"success": True, "data": top_sources}


@router.get("/registry/source-distribution/{source}")
def get_source_details(source: str) -> Dict[str, Any]:
    log.info(f"Fetching details for source: {source}")
    detail_by_verdict = get_source_detail_by_verdict(source)
    detail_by_risk = get_source_detail_by_risk_tier(source)
    return {
        "success": True,
        "source": source,
        "by_verdict": detail_by_verdict,
        "by_risk_tier": detail_by_risk,
    }


@router.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


def run():
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title=SERVICE_NAME)
    app.include_router(router)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()