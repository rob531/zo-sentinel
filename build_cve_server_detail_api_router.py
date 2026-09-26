import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = "cve_server_detail_api"
SERVICE_PORT = 8784
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
WRITE_URL = "http://localhost:8772/write"
HEALTHCHECK_TIMEOUT = 5
REQUEST_TIMEOUT = 30
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)

app = FastAPI(title="CVE Server Detail API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_URL, json={"sql": sql}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table=%s", e, table)
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    try:
        ws_query("SELECT 1 AS ok")
        return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/v1/servers/{server_id}/cves")
def get_server_cves(
    server_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    sql = f"""
    SELECT
        cve_id,
        server_id,
        severity,
        cvss_score,
        description,
        affected_versions,
        published_at,
        references,
        evidence
    FROM mcp_cve_enrichment
    WHERE server_id = '{server_id.replace("'", "''")}'
    ORDER BY published_at DESC NULLS LAST
    LIMIT {limit} OFFSET {offset}
    """
    rows = ws_query(sql)

    count_sql = f"""
    SELECT COUNT(*) AS total
    FROM mcp_cve_enrichment
    WHERE server_id = '{server_id.replace("'", "''")}'
    """
    count_rows = ws_query(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    return {
        "server_id": server_id,
        "cves": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "ts": utc_now_iso(),
    }


@app.get("/api/v1/servers/{server_id}/cve/{cve_id}")
def get_server_cve_detail(server_id: str, cve_id: str):
    sql = f"""
    SELECT
        cve_id,
        server_id,
        severity,
        cvss_score,
        description,
        affected_versions,
        published_at,
        last_modified_at,
        references,
        evidence
    FROM mcp_cve_enrichment
    WHERE server_id = '{server_id.replace("'", "''")}'
      AND cve_id = '{cve_id.replace("'", "''")}'
    LIMIT 1
    """
    rows = ws_query(sql)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CVE {cve_id} not found for server {server_id}",
        )

    threat_sql = f"""
    SELECT
        threat_type,
        severity AS threat_severity,
        evidence,
        reported_at
    FROM mcp_threat_associations
    WHERE server_id = '{server_id.replace("'", "''")}'
    """
    threats = ws_query(threat_sql)

    registry_sql = f"""
    SELECT
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source,
        scan_count
    FROM mcp_server_registry
    WHERE server_id = '{server_id.replace("'", "''")}'
    LIMIT 1
    """
    registry = ws_query(registry_sql)

    return {
        "cve": rows[0],
        "threats": threats,
        "server": registry[0] if registry else None,
        "ts": utc_now_iso(),
    }


@app.get("/api/v1/cves/search")
def search_cves(
    query: str = Query(..., min_length=1),
    severity: Optional[str] = None,
    min_cvss: Optional[float] = Query(default=None, ge=0.0, le=10.0),
    max_cvss: Optional[float] = Query(default=None, ge=0.0, le=10.0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    safe_query = query.replace("'", "''")
    conditions = f"(description ILIKE '%{safe_query}%' OR cve_id ILIKE '%{safe_query}%')"

    if severity:
        conditions += f" AND severity = '{severity.replace("'", "''")}'"
    if min_cvss is not None:
        conditions += f" AND cvss_score >= {min_cvss}"
    if max_cvss is not None:
        conditions += f" AND cvss_score <= {max_cvss}"

    sql = f"""
    SELECT
        cve_id,
        server_id,
        severity,
        cvss_score,
        description,
        affected_versions,
        published_at
    FROM mcp_cve_enrichment
    WHERE {conditions}
    ORDER BY cvss_score DESC NULLS LAST, published_at DESC NULLS LAST
    LIMIT {limit} OFFSET {offset}
    """
    rows = ws_query(sql)

    count_sql = f"SELECT COUNT(*) AS total FROM mcp_cve_enrichment WHERE {conditions}"
    count_rows = ws_query(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    return {
        "query": query,
        "cves": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "ts": utc_now_iso(),
    }


@app.get("/api/v1/cves/summary")
def get_cves_summary():
    severity_sql = """
    SELECT
        severity,
        COUNT(*) AS count,
        AVG(cvss_score) AS avg_cvss
    FROM mcp_cve_enrichment
    GROUP BY severity
    ORDER BY count DESC
    """
    by_severity = ws_query(severity_sql)

    top_sql = """
    SELECT
        server_id,
        COUNT(*) AS cve_count,
        MAX(cvss_score) AS max_cvss
    FROM mcp_cve_enrichment
    GROUP BY server_id
    ORDER BY cve_count DESC
    LIMIT 20
    """
    top_servers = ws_query(top_sql)

    total_sql = "SELECT COUNT(*) AS total_cves, AVG(cvss_score) AS avg_cvss FROM mcp_cve_enrichment"
    total_rows = ws_query(total_sql)
    totals = total_rows[0] if total_rows else {}

    return {
        "total_cves": totals.get("total_cves", 0),
        "avg_cvss": round(totals.get("avg_cvss") or 0, 2),
        "by_severity": by_severity,
        "top_servers": top_servers,
        "ts": utc_now_iso(),
    }


@app.get("/api/v1/servers/{server_id}/risk")
def get_server_risk(server_id: str):
    registry_sql = f"""
    SELECT
        server_id,
        name,
        trust_score,
        verdict,
        risk_tier
    FROM mcp_server_registry
    LEFT JOIN mcp_risk_register USING (server_id)
    WHERE server_id = '{server_id.replace("'", "''")}'
    LIMIT 1
    """
    registry = ws_query(registry_sql)
    if not registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    cve_count_sql = f"""
    SELECT COUNT(*) AS cve_count, MAX(cvss_score) AS max_cvss
    FROM mcp_cve_enrichment
    WHERE server_id = '{server_id.replace("'", "''")}'
    """
    cve_rows = ws_query(cve_count_sql)
    cve_stats = cve_rows[0] if cve_rows else {}

    threat_count_sql = f"""
    SELECT COUNT(*) AS threat_count
    FROM mcp_threat_associations
    WHERE server_id = '{server_id.replace("'", "''")}'
    """
    threat_rows = ws_query(threat_count_sql)
    threat_count = threat_rows[0]["threat_count"] if threat_rows else 0

    return {
        "server": registry[0],
        "cve_count": cve_stats.get("cve_count", 0),
        "max_cvss": cve_stats.get("max_cvss"),
        "threat_count": threat_count,
        "ts": utc_now_iso(),
    }


def send_heartbeat():
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": "ok",
        "last_heartbeat": utc_now_iso(),
        "meta": "{}",
    }])


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error("Heartbeat failed: %s", e)
        time.sleep(60)


if __name__ == "__main__":
    import threading
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    log.info("Starting %s on port %d", SERVICE_NAME, SERVICE_PORT)
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")