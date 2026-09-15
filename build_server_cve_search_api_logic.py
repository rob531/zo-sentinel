import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename="/home/workspace/logs/build_server_cve_search_api_logic.log",
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "build_server_cve_search_api_logic"
PORT = 8789
HEARTBEAT_INTERVAL = 60

app = FastAPI()

CVE_SEVERITY_LEVELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed for table=%s: %s", table, e)
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def ensure_tables() -> None:
    cve_exposure_table = """
    CREATE TABLE IF NOT EXISTS mcp_cve_exposure (
        id INTEGER PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        cve_id VARCHAR NOT NULL,
        severity VARCHAR NOT NULL,
        description TEXT,
        affected_versions TEXT,
        fixed_versions TEXT,
        cvss_score DOUBLE,
        published_at VARCHAR,
        last_modified_at VARCHAR,
        exposure_level VARCHAR,
        evidence TEXT,
        discovered_at VARCHAR,
        UNIQUE(server_id, cve_id)
    )
    """
    ws_execute(cve_exposure_table)

    cve_exposure_history_table = """
    CREATE TABLE IF NOT EXISTS mcp_cve_exposure_history (
        id INTEGER PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        cve_id VARCHAR NOT NULL,
        severity VARCHAR NOT NULL,
        exposure_level VARCHAR,
        action_taken VARCHAR,
        resolved_at VARCHAR,
        created_at VARCHAR NOT NULL
    )
    """
    ws_execute(cve_exposure_history_table)
    log.info("CVE exposure tables ensured")


def search_cve_exposure(
    server_id: Optional[str] = None,
    cve_id: Optional[str] = None,
    severity: Optional[str] = None,
    exposure_level: Optional[str] = None,
    min_cvss: Optional[float] = None,
    max_cvss: Optional[float] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    conditions = []
    params = {}

    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if cve_id:
        conditions.append("cve_id ILIKE :cve_id")
        params["cve_id"] = f"%{cve_id}%"
    if severity and severity in CVE_SEVERITY_LEVELS:
        conditions.append("severity = :severity")
        params["severity"] = severity
    if exposure_level:
        conditions.append("exposure_level = :exposure_level")
        params["exposure_level"] = exposure_level
    if min_cvss is not None:
        conditions.append("cvss_score >= :min_cvss")
        params["min_cvss"] = min_cvss
    if max_cvss is not None:
        conditions.append("cvss_score <= :max_cvss")
        params["max_cvss"] = max_cvss

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as total FROM mcp_cve_exposure WHERE {where_clause}"
    count_result = ws_query(count_sql)
    total = count_result[0]["total"] if count_result else 0

    search_sql = f"""
    SELECT
        e.server_id,
        e.cve_id,
        e.severity,
        e.description,
        e.affected_versions,
        e.fixed_versions,
        e.cvss_score,
        e.published_at,
        e.last_modified_at,
        e.exposure_level,
        e.evidence,
        e.discovered_at,
        r.name as server_name,
        r.verdict,
        r.trust_score
    FROM mcp_cve_exposure e
    LEFT JOIN mcp_server_registry r ON e.server_id = r.server_id
    WHERE {where_clause}
    ORDER BY
        CASE e.severity
            WHEN 'CRITICAL' THEN 1
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            WHEN 'LOW' THEN 4
            ELSE 5
        END,
        e.cvss_score DESC NULLS LAST
    LIMIT :limit OFFSET :offset
    """
    params["limit"] = limit
    params["offset"] = offset

    rows = ws_query(search_sql)

    return {
        "results": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "server_id": server_id,
            "cve_id": cve_id,
            "severity": severity,
            "exposure_level": exposure_level,
            "min_cvss": min_cvss,
            "max_cvss": max_cvss,
        },
    }


def get_cve_detail(cve_id: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM mcp_cve_exposure WHERE cve_id = ? LIMIT 1"
    rows = ws_query(sql)
    if not rows:
        return None

    cve = rows[0]

    affected_servers_sql = """
    SELECT server_id, severity, exposure_level, cvss_score, discovered_at
    FROM mcp_cve_exposure
    WHERE cve_id = ?
    ORDER BY
        CASE severity
            WHEN 'CRITICAL' THEN 1
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            WHEN 'LOW' THEN 4
            ELSE 5
        END
    """
    affected_rows = ws_query(affected_servers_sql)

    history_sql = """
    SELECT * FROM mcp_cve_exposure_history
    WHERE cve_id = ?
    ORDER BY created_at DESC
    LIMIT 50
    """
    history_rows = ws_query(history_sql)

    cve["affected_servers"] = affected_rows
    cve["exposure_history"] = history_rows

    return cve


def get_server_cve_summary(server_id: str) -> Dict[str, Any]:
    count_sql = "SELECT COUNT(*) as total FROM mcp_cve_exposure WHERE server_id = ?"
    count_rows = ws_query(count_sql)
    total = count_rows[0]["total"] if count_rows else 0

    severity_sql = """
    SELECT severity, COUNT(*) as count
    FROM mcp_cve_exposure
    WHERE server_id = ?
    GROUP BY severity
    ORDER BY
        CASE severity
            WHEN 'CRITICAL' THEN 1
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            WHEN 'LOW' THEN 4
            ELSE 5
        END
    """
    severity_rows = ws_query(severity_sql)

    exposure_level_sql = """
    SELECT exposure_level, COUNT(*) as count
    FROM mcp_cve_exposure
    WHERE server_id = ?
    GROUP BY exposure_level
    """
    exposure_rows = ws_query(exposure_level_sql)

    critical_cvss_sql = """
    SELECT cve_id, cvss_score, severity, description
    FROM mcp_cve_exposure
    WHERE server_id = ? AND severity IN ('CRITICAL', 'HIGH')
    ORDER BY cvss_score DESC NULLS LAST
    LIMIT 10
    """
    critical_rows = ws_query(critical_cvss_sql)

    recent_sql = """
    SELECT cve_id, severity, cvss_score, discovered_at
    FROM mcp_cve_exposure
    WHERE server_id = ?
    ORDER BY discovered_at DESC
    LIMIT 20
    """
    recent_rows = ws_query(recent_sql)

    return {
        "server_id": server_id,
        "total_cves": total,
        "by_severity": {r["severity"]: r["count"] for r in severity_rows},
        "by_exposure_level": {r["exposure_level"]: r["count"] for r in exposure_rows},
        "critical_high": critical_rows,
        "recent_discoveries": recent_rows,
    }


def record_cve_exposure(
    server_id: str,
    cve_id: str,
    severity: str,
    description: Optional[str] = None,
    affected_versions: Optional[str] = None,
    fixed_versions: Optional[str] = None,
    cvss_score: Optional[float] = None,
    published_at: Optional[str] = None,
    last_modified_at: Optional[str] = None,
    exposure_level: str = "POTENTIAL",
    evidence: Optional[str] = None,
) -> bool:
    now = utc_now_iso()

    upsert_sql = """
    INSERT INTO mcp_cve_exposure (
        server_id, cve_id, severity, description, affected_versions,
        fixed_versions, cvss_score, published_at, last_modified_at,
        exposure_level, evidence, discovered_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(server_id, cve_id) DO UPDATE SET
        severity = excluded.severity,
        description = excluded.description,
        affected_versions = excluded.affected_versions,
        fixed_versions = excluded.fixed_versions,
        cvss_score = excluded.cvss_score,
        last_modified_at = excluded.last_modified_at,
        exposure_level = excluded.exposure_level,
        evidence = excluded.evidence
    """

    params = [
        server_id,
        cve_id,
        severity,
        description,
        affected_versions,
        fixed_versions,
        cvss_score,
        published_at,
        last_modified_at,
        exposure_level,
        evidence,
        now,
    ]

    if ws_execute(upsert_sql):
        history_sql = """
        INSERT INTO mcp_cve_exposure_history (
            server_id, cve_id, severity, exposure_level, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """
        ws_execute(history_sql)
        return True
    return False


def get_cve_severity_trends(days: int = 30) -> Dict[str, Any]:
    severity_trend_sql = """
    SELECT
        severity,
        DATE(created_at) as date,
        COUNT(*) as count
    FROM mcp_cve_exposure_history
    WHERE created_at >= DATE_SUB(CURRENT_DATE, INTERVAL ? DAY)
    GROUP BY severity, DATE(created_at)
    ORDER BY date ASC
    """
    trend_rows = ws_query(severity_trend_sql)

    summary_sql = """
    SELECT
        severity,
        COUNT(*) as total_count,
        COUNT(DISTINCT server_id) as affected_servers
    FROM mcp_cve_exposure
    GROUP BY severity
    """
    summary_rows = ws_query(summary_sql)

    return {
        "trends": trend_rows,
        "current_summary": {
            r["severity"]: {
                "total": r["total_count"],
                "affected_servers": r["affected_servers"],
            }
            for r in summary_rows
        },
        "period_days": days,
    }


def resolve_cve_exposure(
    server_id: str,
    cve_id: str,
    action_taken: str = "RESOLVED",
    notes: Optional[str] = None,
) -> bool:
    now = utc_now_iso()

    update_sql = """
    UPDATE mcp_cve_exposure
    SET exposure_level = 'RESOLVED'
    WHERE server_id = ? AND cve_id = ?
    """
    if not ws_execute(update_sql):
        return False

    history_sql = """
    INSERT INTO mcp_cve_exposure_history (
        server_id, cve_id, severity, exposure_level, action_taken, resolved_at, created_at
    ) VALUES (?, ?, ?, 'RESOLVED', ?, ?, ?)
    """
    ws_execute(history_sql)

    log.info("CVE exposure resolved: server_id=%s cve_id=%s action=%s", server_id, cve_id, action_taken)
    return True


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/api/v1/cve/search")
def cve_search(
    server_id: Optional[str] = None,
    cve_id: Optional[str] = None,
    severity: Optional[str] = None,
    exposure_level: Optional[str] = None,
    min_cvss: Optional[float] = None,
    max_cvss: Optional[float] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
):
    result = search_cve_exposure(
        server_id=server_id,
        cve_id=cve_id,
        severity=severity,
        exposure_level=exposure_level,
        min_cvss=min_cvss,
        max_cvss=max_cvss,
        limit=limit,
        offset=offset,
    )
    return result


@app.get("/api/v1/cve/{cve_id}")
def cve_detail(cve_id: str):
    detail = get_cve_detail(cve_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"CVE not found: {cve_id}")
    return detail


@app.get("/api/v1/server/{server_id}/cve/summary")
def server_cve_summary(server_id: str):
    summary = get_server_cve_summary(server_id)
    return summary


@app.post("/api/v1/cve/exposure")
def record_exposure(
    server_id: str,
    cve_id: str,
    severity: str,
    description: Optional[str] = None,
    affected_versions: Optional[str] = None,
    fixed_versions: Optional[str] = None,
    cvss_score: Optional[float] = None,
    published_at: Optional[str] = None,
    last_modified_at: Optional[str] = None,
    exposure_level: str = "POTENTIAL",
    evidence: Optional[str] = None,
):
    if severity not in CVE_SEVERITY_LEVELS:
        raise HTTPException(status_code=400, detail=f"Invalid severity: {severity}")

    success = record_cve_exposure(
        server_id=server_id,
        cve_id=cve_id,
        severity=severity,
        description=description,
        affected_versions=affected_versions,
        fixed_versions=fixed_versions,
        cvss_score=cvss_score,
        published_at=published_at,
        last_modified_at=last_modified_at,
        exposure_level=exposure_level,
        evidence=evidence,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to record CVE exposure")

    return {"status": "recorded", "server_id": server_id, "cve_id": cve_id, "ts": utc_now_iso()}


@app.post("/api/v1/cve/{server_id}/{cve_id}/resolve")
def resolve_exposure(
    server_id: str,
    cve_id: str,
    action_taken: str = "RESOLVED",
    notes: Optional[str] = None,
):
    success = resolve_cve_exposure(server_id, cve_id, action_taken, notes)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to resolve CVE exposure")
    return {"status": "resolved", "server_id": server_id, "cve_id": cve_id, "ts": utc_now_iso()}


@app.get("/api/v1/cve/trends")
def cve_trends(days: int = Query(default=30, ge=1, le=365)):
    trends = get_cve_severity_trends(days)
    return trends


def run():
    ensure_tables()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()