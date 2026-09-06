import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("/home/workspace/logs/risk_tier_trend_dashboard.log")],
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "risk_tier_trend_dashboard_router"
PORT = 8790

router = APIRouter(prefix="/api/risk-tier-trend", tags=["risk-tier-trend"])


def ws_query(sql: str) -> list:
    """Query DuckDB via write_service."""
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.RequestException as e:
        log.error(f"Query failed for SQL: {sql[:200]}: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    """Write to DuckDB via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error(f"Write failed for table {table}: {e}")
        return False


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_risk_tier_distribution() -> dict:
    """Get current distribution of servers across risk tiers."""
    sql = """
        SELECT 
            COALESCE(r.risk_tier, 'UNKNOWN') as risk_tier,
            COUNT(DISTINCT r.server_id) as server_count,
            ROUND(COUNT(DISTINCT r.server_id) * 100.0 / NULLIF(
                (SELECT COUNT(DISTINCT server_id) FROM mcp_risk_register), 0
            ), 2) as percentage
        FROM mcp_risk_register r
        GROUP BY r.risk_tier
        ORDER BY 
            CASE r.risk_tier
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                WHEN 'INFO' THEN 5
                ELSE 6
            END
    """
    return ws_query(sql)


def get_risk_tier_trends(days: int = 30) -> list:
    """Get risk tier trend data over specified days."""
    sql = f"""
        WITH date_range AS (
            SELECT generate_series(
                CURRENT_DATE - INTERVAL '{days} days',
                CURRENT_DATE,
                INTERVAL '1 day'
            ) as date
        ),
        daily_counts AS (
            SELECT 
                dr.date,
                COALESCE(rr.risk_tier, 'UNKNOWN') as risk_tier,
                COUNT(DISTINCT rr.server_id) as server_count
            FROM date_range dr
            LEFT JOIN mcp_risk_register rr 
                ON DATE(rr.computed_at) <= dr.date
            GROUP BY dr.date, rr.risk_tier
        )
        SELECT 
            date,
            risk_tier,
            server_count,
            SUM(server_count) OVER (PARTITION BY date ORDER BY risk_tier ROWS UNBOUNDED PRECEDING) as cumulative_count
        FROM daily_counts
        ORDER BY date, 
            CASE risk_tier
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                WHEN 'INFO' THEN 5
                ELSE 6
            END
    """
    return ws_query(sql)


def get_verdict_distribution_by_risk_tier() -> list:
    """Get verdict distribution within each risk tier."""
    sql = """
        SELECT 
            COALESCE(r.risk_tier, 'UNKNOWN') as risk_tier,
            COALESCE(s.verdict, 'UNKNOWN') as verdict,
            COUNT(DISTINCT r.server_id) as server_count
        FROM mcp_risk_register r
        LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
        GROUP BY r.risk_tier, s.verdict
        ORDER BY r.risk_tier, server_count DESC
    """
    return ws_query(sql)


def get_high_risk_servers(limit: int = 50) -> list:
    """Get servers with highest risk (CRITICAL/HIGH tier)."""
    sql = f"""
        SELECT 
            r.server_id,
            COALESCE(s.name, r.server_id) as name,
            r.risk_tier,
            r.risk_rank,
            r.threat_count,
            r.computed_at,
            COALESCE(s.verdict, 'UNKNOWN') as verdict,
            COALESCE(s.trust_score, 0) as trust_score
        FROM mcp_risk_register r
        LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
        WHERE r.risk_tier IN ('CRITICAL', 'HIGH')
        ORDER BY 
            CASE r.risk_tier WHEN 'CRITICAL' THEN 1 ELSE 2 END,
            r.risk_rank DESC,
            r.threat_count DESC
        LIMIT {limit}
    """
    return ws_query(sql)


def get_risk_tier_summary() -> dict:
    """Get summary statistics for risk tiers."""
    sql = """
        SELECT 
            COUNT(DISTINCT server_id) as total_servers,
            COUNT(DISTINCT CASE WHEN risk_tier = 'CRITICAL' THEN server_id END) as critical_count,
            COUNT(DISTINCT CASE WHEN risk_tier = 'HIGH' THEN server_id END) as high_count,
            COUNT(DISTINCT CASE WHEN risk_tier = 'MEDIUM' THEN server_id END) as medium_count,
            COUNT(DISTINCT CASE WHEN risk_tier = 'LOW' THEN server_id END) as low_count,
            COUNT(DISTINCT CASE WHEN risk_tier = 'INFO' THEN server_id END) as info_count,
            COUNT(DISTINCT CASE WHEN risk_tier IS NULL THEN server_id END) as unknown_count,
            MAX(computed_at) as last_computed_at
        FROM mcp_risk_register
    """
    result = ws_query(sql)
    if result:
        return result[0]
    return {
        "total_servers": 0,
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "info_count": 0,
        "unknown_count": 0,
        "last_computed_at": None,
    }


def get_risk_tier_trend_summary(days: int = 30) -> list:
    """Get daily summary of risk tier changes."""
    sql = f"""
        WITH RECURSIVE date_series AS (
            SELECT CURRENT_DATE - INTERVAL '{days} days' as date
            UNION ALL
            SELECT date + INTERVAL '1 day'
            FROM date_series
            WHERE date < CURRENT_DATE
        ),
        daily_snapshot AS (
            SELECT 
                ds.date,
                COUNT(DISTINCT CASE WHEN r.risk_tier = 'CRITICAL' THEN r.server_id END) as critical,
                COUNT(DISTINCT CASE WHEN r.risk_tier = 'HIGH' THEN r.server_id END) as high,
                COUNT(DISTINCT CASE WHEN r.risk_tier = 'MEDIUM' THEN r.server_id END) as medium,
                COUNT(DISTINCT CASE WHEN r.risk_tier = 'LOW' THEN r.server_id END) as low,
                COUNT(DISTINCT CASE WHEN r.risk_tier = 'INFO' THEN r.server_id END) as info,
                COUNT(DISTINCT r.server_id) as total
            FROM date_series ds
            LEFT JOIN mcp_risk_register r ON DATE(r.computed_at) <= ds.date
            GROUP BY ds.date
        )
        SELECT 
            date,
            critical,
            high,
            medium,
            low,
            info,
            total,
            CASE WHEN LAG(total) OVER (ORDER BY date) > 0 
                 THEN ROUND((total - LAG(total) OVER (ORDER BY date)) * 100.0 / LAG(total) OVER (ORDER BY date), 2)
                 ELSE 0 
            END as total_change_pct,
            CASE WHEN LAG(critical) OVER (ORDER BY date) > 0 
                 THEN ROUND((critical - LAG(critical) OVER (ORDER BY date)) * 100.0 / LAG(critical) OVER (ORDER BY date), 2)
                 ELSE 0 
            END as critical_change_pct
        FROM daily_snapshot
        ORDER BY date
    """
    return ws_query(sql)


@router.get("/distribution")
def get_distribution():
    """Get current risk tier distribution."""
    try:
        distribution = get_risk_tier_distribution()
        summary = get_risk_tier_summary()
        return {
            "status": "ok",
            "timestamp": utc_now_iso(),
            "distribution": distribution,
            "summary": summary,
        }
    except Exception as e:
        log.error(f"Failed to get distribution: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends")
def get_trends(days: int = Query(default=30, ge=1, le=365)):
    """Get risk tier trends over time."""
    try:
        trends = get_risk_tier_trends(days)
        trend_summary = get_risk_tier_trend_summary(days)
        return {
            "status": "ok",
            "timestamp": utc_now_iso(),
            "days": days,
            "trends": trends,
            "trend_summary": trend_summary,
        }
    except Exception as e:
        log.error(f"Failed to get trends: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
def get_verdicts_by_tier():
    """Get verdict distribution within each risk tier."""
    try:
        verdicts = get_verdict_distribution_by_risk_tier()
        return {
            "status": "ok",
            "timestamp": utc_now_iso(),
            "verdicts_by_tier": verdicts,
        }
    except Exception as e:
        log.error(f"Failed to get verdicts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/high-risk")
def get_high_risk(
    limit: int = Query(default=50, ge=1, le=500),
    tier: Optional[str] = Query(default=None),
):
    """Get servers with highest risk."""
    try:
        servers = get_high_risk_servers(limit)
        if tier:
            servers = [s for s in servers if s.get("risk_tier") == tier.upper()]
        return {
            "status": "ok",
            "timestamp": utc_now_iso(),
            "count": len(servers),
            "servers": servers,
        }
    except Exception as e:
        log.error(f"Failed to get high risk servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
def get_summary():
    """Get risk tier summary statistics."""
    try:
        summary = get_risk_tier_summary()
        distribution = get_risk_tier_distribution()
        return {
            "status": "ok",
            "timestamp": utc_now_iso(),
            "summary": summary,
            "distribution": distribution,
        }
    except Exception as e:
        log.error(f"Failed to get summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


app = router
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)