import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from build_server_cve_search_api_logic import (
    ensure_tables,
    format_cve_result,
    search_cves_by_keyword,
    search_cves_by_server_id,
    utc_now_iso,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename="/home/workspace/logs/cve_search_api_router.log",
)
log = logging.getLogger("cve_search_api_router")

router = APIRouter(prefix="/api/cve-search", tags=["cve-search"])

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"


class CVEKeywordSearchRequest(BaseModel):
    keyword: str
    limit: int = 50
    offset: int = 0


class CVEServerSearchRequest(BaseModel):
    server_id: str
    limit: int = 50


def ws_query(sql: str) -> List[Dict[str, Any]]:
    import requests

    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    import requests

    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    import requests

    try:
        resp = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


@router.on_event("startup")
async def startup_event():
    log.info("CVE Search API Router starting up")
    ensure_tables(ws_execute, log)
    log.info("CVE Search API Router ready")


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cve_search_api_router", "ts": utc_now_iso()}


@router.get("/search/keyword")
async def search_by_keyword(
    keyword: str = Query(..., description="Keyword to search in CVE records"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    try:
        results = search_cves_by_keyword(
            keyword=keyword,
            limit=limit,
            offset=offset,
            ws_query=ws_query,
            log=log,
        )
        formatted = [format_cve_result(r) for r in results]
        return {
            "success": True,
            "keyword": keyword,
            "count": len(formatted),
            "results": formatted,
            "ts": utc_now_iso(),
        }
    except Exception as e:
        log.error(f"Keyword search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/server/{server_id}")
async def search_by_server(
    server_id: str,
    limit: int = Query(50, ge=1, le=500, description="Maximum results to return"),
):
    try:
        results = search_cves_by_server_id(
            server_id=server_id,
            limit=limit,
            ws_query=ws_query,
            log=log,
        )
        formatted = [format_cve_result(r) for r in results]
        return {
            "success": True,
            "server_id": server_id,
            "count": len(formatted),
            "results": formatted,
            "ts": utc_now_iso(),
        }
    except Exception as e:
        log.error(f"Server CVE search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cve/{cve_id}")
async def get_cve_detail(cve_id: str):
    sql = f"""
        SELECT * FROM mcp_cve_records
        WHERE cve_id = '{cve_id.replace("'", "''")}'
        LIMIT 1
    """
    results = ws_query(sql)
    if not results:
        raise HTTPException(status_code=404, detail=f"CVE {cve_id} not found")
    return {
        "success": True,
        "result": format_cve_result(results[0]),
        "ts": utc_now_iso(),
    }


@router.get("/stats")
async def get_cve_stats():
    total_sql = "SELECT COUNT(*) as total FROM mcp_cve_records"
    critical_sql = "SELECT COUNT(*) as critical FROM mcp_cve_records WHERE severity = 'CRITICAL'"
    high_sql = "SELECT COUNT(*) as high FROM mcp_cve_records WHERE severity = 'HIGH'"
    affected_sql = "SELECT COUNT(DISTINCT server_id) as affected_servers FROM mcp_cve_associations"

    try:
        total_results = ws_query(total_sql)
        critical_results = ws_query(critical_sql)
        high_results = ws_query(high_sql)
        affected_results = ws_query(affected_sql)

        return {
            "success": True,
            "stats": {
                "total_cves": total_results[0].get("total", 0) if total_results else 0,
                "critical_count": critical_results[0].get("critical", 0) if critical_results else 0,
                "high_count": high_results[0].get("high", 0) if high_results else 0,
                "affected_servers": affected_results[0].get("affected_servers", 0) if affected_results else 0,
            },
            "ts": utc_now_iso(),
        }
    except Exception as e:
        log.error(f"Stats query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    log.info("Starting CVE Search API Router on port 8790")
    uvicorn.run(router, host="0.0.0.0", port=8790)