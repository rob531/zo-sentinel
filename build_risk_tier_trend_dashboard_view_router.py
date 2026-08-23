import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any

from sentinel_sdk import ZoSentinelClient

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
WRITE_URL = "http://localhost:8772/write"

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "risk_tier_trend_dashboard_view_router.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()],
)
log = logging.getLogger("risk_tier_trend_dashboard_view_router")

SERVICE_NAME = "risk_tier_trend_dashboard_view_router"
PORT = 8792

client = ZoSentinelClient(base_url=WRITE_SERVICE_URL)

router = APIRouter()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service."""
    import requests
    resp = requests.post(
        QUERY_URL, json={"sql": sql}, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write rows to a table via write_service."""
    import requests
    resp = requests.post(
        WRITE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/risk-tier-trends/summary")
def get_risk_tier_summary(
    days: int = Query(default=30, ge=1, le=365, description="Number of days to analyze"),
    granularity: str = Query(default="day", regex="^(day|week|month)$", description="Time granularity")
) -> Dict[str, Any]:
    """
    Get summary of risk tier distribution over time.
    Returns current distribution and trend direction for each tier.
    """
    try:
        interval_clause = "1 DAY" if granularity == "day" else ("1 WEEK" if granularity == "week" else "1 MONTH")
        
        sql = f"""
        WITH daily_tiers AS (
            SELECT 
                date_trunc('{granularity}', computed_at::TIMESTAMPTZ) as period,
                risk_tier,
                COUNT(*) as server_count
            FROM mcp_risk_register
            WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
            GROUP BY 1, 2
        ),
        ranked_tiers AS (
            SELECT 
                period,
                risk_tier,
                server_count,
                ROW_NUMBER() OVER (PARTITION BY period ORDER BY period DESC, server_count DESC) as rn
            FROM daily_tiers
        ),
        current_dist AS (
            SELECT risk_tier, server_count
            FROM ranked_tiers
            WHERE rn = 1
        ),
        previous_dist AS (
            SELECT risk_tier, server_count
            FROM daily_tiers
            WHERE period = (
                SELECT MAX(period) - INTERVAL '{interval_clause}' FROM daily_tiers
            )
        ),
        trend_data AS (
            SELECT 
                dt1.risk_tier,
                dt1.server_count as current_count,
                COALESCE(dt2.server_count, 0) as previous_count,
                dt1.server_count - COALESCE(dt2.server_count, 0) as delta,
                CASE 
                    WHEN COALESCE(dt2.server_count, 0) = 0 THEN 0
                    ELSE ROUND(100.0 * (dt1.server_count - COALESCE(dt2.server_count, 0)) / dt2.server_count, 2)
                END as percent_change
            FROM current_dist dt1
            LEFT JOIN previous_dist dt2 ON dt1.risk_tier = dt2.risk_tier
        )
        SELECT 
            risk_tier,
            current_count,
            previous_count,
            delta,
            percent_change,
            CASE 
                WHEN percent_change > 5 THEN 'increasing'
                WHEN percent_change < -5 THEN 'decreasing'
                ELSE 'stable'
            END as trend_direction
        FROM trend_data
        ORDER BY 
            CASE risk_tier 
                WHEN 'CRITICAL' THEN 1 
                WHEN 'HIGH' THEN 2 
                WHEN 'MEDIUM' THEN 3 
                WHEN 'LOW' THEN 4 
                ELSE 5 
            END
        """
        
        rows = ws_query(sql)
        
        total_servers = sum(r.get("current_count", 0) for r in rows)
        
        return {
            "summary": {
                "total_servers": total_servers,
                "period_days": days,
                "granularity": granularity,
                "generated_at": utc_now_iso()
            },
            "distribution": rows
        }
        
    except Exception as e:
        log.error(f"Error fetching risk tier summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/timeseries")
def get_risk_tier_timeseries(
    days: int = Query(default=30, ge=1, le=365),
    risk_tier: Optional[str] = Query(default=None, description="Filter by specific risk tier"),
    granularity: str = Query(default="day", regex="^(day|week|month)$")
) -> Dict[str, Any]:
    """
    Get time series data for risk tier distribution.
    Useful for charting risk tier changes over time.
    """
    try:
        tier_filter = f"AND risk_tier = '{risk_tier}'" if risk_tier else ""
        
        sql = f"""
        SELECT 
            date_trunc('{granularity}', computed_at::TIMESTAMPTZ) as period,
            risk_tier,
            COUNT(*) as server_count,
            AVG(trust_score) as avg_trust_score,
            MAX(threat_count) as max_threats
        FROM mcp_risk_register
        WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        {tier_filter}
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
        
        rows = ws_query(sql)
        
        periods = sorted(set(r.get("period") for r in rows))
        tiers = sorted(set(r.get("risk_tier") for r in rows))
        
        timeseries = {}
        for tier in tiers:
            timeseries[tier] = [
                {
                    "period": p,
                    "server_count": next((r.get("server_count", 0) for r in rows if r.get("period") == p and r.get("risk_tier") == tier), 0),
                    "avg_trust_score": next((round(r.get("avg_trust_score", 0), 2) for r in rows if r.get("period") == p and r.get("risk_tier") == tier), 0)
                }
                for p in periods
            ]
        
        return {
            "periods": [str(p) for p in periods],
            "tiers": list(tiers),
            "timeseries": timeseries,
            "generated_at": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error fetching risk tier timeseries: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/tier/{tier}")
def get_tier_details(
    tier: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=1000)
) -> Dict[str, Any]:
    """
    Get detailed information about a specific risk tier.
    Returns servers in that tier and their recent changes.
    """
    try:
        valid_tiers = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        if tier.upper() not in valid_tiers:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {valid_tiers}")
        
        tier = tier.upper()
        
        sql = f"""
        WITH tier_servers AS (
            SELECT 
                rr.server_id,
                rr.risk_tier,
                rr.trust_score,
                rr.threat_count,
                rr.risk_rank,
                rr.computed_at,
                r.name as server_name,
                r.url as server_url,
                r.verdict
            FROM mcp_risk_register rr
            JOIN mcp_server_registry r ON rr.server_id = r.server_id
            WHERE rr.risk_tier = '{tier}'
              AND rr.computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        ),
        ranked AS (
            SELECT 
                *,
                ROW_NUMBER() OVER (ORDER BY risk_rank DESC, threat_count DESC) as rn
            FROM tier_servers
        )
        SELECT server_id, server_name, server_url, verdict, trust_score, threat_count, risk_rank, computed_at
        FROM ranked
        WHERE rn <= {limit}
        """
        
        servers = ws_query(sql)
        
        summary_sql = f"""
        SELECT 
            COUNT(DISTINCT server_id) as total_servers,
            ROUND(AVG(trust_score), 2) as avg_trust_score,
            SUM(threat_count) as total_threats,
            COUNT(DISTINCT verdict) as verdict_diversity
        FROM mcp_risk_register
        WHERE risk_tier = '{tier}'
          AND computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        """
        
        summary = ws_query(summary_sql)
        
        return {
            "tier": tier,
            "period_days": days,
            "summary": summary[0] if summary else {},
            "servers": servers,
            "count": len(servers),
            "generated_at": utc_now_iso()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error fetching tier details for {tier}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/escalations")
def get_escalations(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=500)
) -> Dict[str, Any]:
    """
    Track servers that have escalated to higher risk tiers.
    Detects concerning trend of increasing risk.
    """
    try:
        sql = f"""
        WITH current_tier AS (
            SELECT server_id, risk_tier, risk_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
            WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        ),
        previous_tier AS (
            SELECT server_id, risk_tier, risk_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at ASC) as rn
            FROM mcp_risk_register
            WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days * 2} DAYS')
              AND computed_at < (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        ),
        escalated AS (
            SELECT 
                c.server_id,
                p.risk_tier as from_tier,
                c.risk_tier as to_tier,
                p.risk_rank as from_rank,
                c.risk_rank as to_rank,
                c.risk_rank - p.risk_rank as rank_change,
                c.computed_at as escalated_at,
                r.name as server_name,
                r.url as server_url
            FROM current_tier c
            JOIN previous_tier p ON c.server_id = p.server_id AND p.rn = 1
            JOIN mcp_server_registry r ON c.server_id = r.server_id
            WHERE c.rn = 1
              AND c.risk_rank > p.risk_rank
            ORDER BY rank_change DESC, c.computed_at DESC
        )
        SELECT * FROM escalated LIMIT {limit}
        """
        
        escalations = ws_query(sql)
        
        return {
            "period_days": days,
            "escalation_count": len(escalations),
            "escalations": escalations,
            "generated_at": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error fetching escalations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/de-escalations")
def get_de_escalations(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=500)
) -> Dict[str, Any]:
    """
    Track servers that have de-escalated to lower risk tiers.
    Positive indicator of improving security posture.
    """
    try:
        sql = f"""
        WITH current_tier AS (
            SELECT server_id, risk_tier, risk_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
            WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        ),
        previous_tier AS (
            SELECT server_id, risk_tier, risk_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at ASC) as rn
            FROM mcp_risk_register
            WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days * 2} DAYS')
              AND computed_at < (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        ),
        de_escalated AS (
            SELECT 
                c.server_id,
                p.risk_tier as from_tier,
                c.risk_tier as to_tier,
                p.risk_rank as from_rank,
                c.risk_rank as to_rank,
                p.risk_rank - c.risk_rank as rank_change,
                c.computed_at as improved_at,
                r.name as server_name,
                r.url as server_url
            FROM current_tier c
            JOIN previous_tier p ON c.server_id = p.server_id AND p.rn = 1
            JOIN mcp_server_registry r ON c.server_id = r.server_id
            WHERE c.rn = 1
              AND c.risk_rank < p.risk_rank
            ORDER BY rank_change DESC, c.computed_at DESC
        )
        SELECT * FROM de_escalated LIMIT {limit}
        """
        
        de_escalations = ws_query(sql)
        
        return {
            "period_days": days,
            "de_escalation_count": len(de_escalations),
            "de_escalations": de_escalations,
            "generated_at": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error fetching de-escalations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/heatmap")
def get_risk_tier_heatmap(
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="day", regex="^(day|week|month)$")
) -> Dict[str, Any]:
    """
    Generate a heatmap matrix of risk tier distribution over time.
    Useful for visual dashboards showing risk concentration changes.
    """
    try:
        sql = f"""
        SELECT 
            date_trunc('{granularity}', computed_at::TIMESTAMPTZ) as period,
            risk_tier,
            COUNT(*) as server_count,
            ROUND(AVG(trust_score), 2) as avg_trust_score,
            SUM(threat_count) as total_threats
        FROM mcp_risk_register
        WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        GROUP BY 1, 2
        ORDER BY 1, 
            CASE risk_tier 
                WHEN 'CRITICAL' THEN 1 
                WHEN 'HIGH' THEN 2 
                WHEN 'MEDIUM' THEN 3 
                WHEN 'LOW' THEN 4 
                ELSE 5 
            END
        """
        
        rows = ws_query(sql)
        
        periods = sorted(set(r.get("period") for r in rows))
        tiers = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        
        heatmap = {}
        for tier in tiers:
            tier_data = [r for r in rows if r.get("risk_tier") == tier]
            heatmap[tier] = [
                {
                    "period": str(p),
                    "server_count": next((r.get("server_count", 0) for r in tier_data if str(r.get("period")) == str(p)), 0),
                    "avg_trust_score": next((r.get("avg_trust_score", 0) for r in tier_data if str(r.get("period")) == str(p)), 0),
                    "severity_intensity": next(
                        (r.get("server_count", 0) / max(sum(t.get("server_count", 0) for t in rows if str(t.get("period")) == str(p)), 1)
                        for r in tier_data if str(r.get("period")) == str(p)), 0
                    )
                }
                for p in periods
            ]
        
        return {
            "periods": [str(p) for p in periods],
            "tiers": tiers,
            "heatmap": heatmap,
            "granularity": granularity,
            "generated_at": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error generating risk tier heatmap: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/risk-tier-trends/report")
def generate_risk_trend_report(
    days: int = Query(default=30, ge=1, le=365)
) -> Dict[str, Any]:
    """
    Generate a comprehensive risk tier trend report.
    Includes summary, trends, and recommendations.
    """
    try:
        summary_sql = f"""
        SELECT 
            risk_tier,
            COUNT(*) as server_count,
            ROUND(AVG(trust_score), 2) as avg_trust_score,
            ROUND(AVG(threat_count), 1) as avg_threat_count
        FROM mcp_risk_register
        WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        GROUP BY risk_tier
        ORDER BY 
            CASE risk_tier 
                WHEN 'CRITICAL' THEN 1 
                WHEN 'HIGH' THEN 2 
                WHEN 'MEDIUM' THEN 3 
                WHEN 'LOW' THEN 4 
                ELSE 5 
            END
        """
        
        distribution = ws_query(summary_sql)
        
        overall_sql = f"""
        SELECT 
            COUNT(DISTINCT server_id) as total_servers,
            ROUND(AVG(trust_score), 2) as global_avg_trust_score,
            SUM(threat_count) as total_threats
        FROM mcp_risk_register
        WHERE computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        """
        
        overall = ws_query(overall_sql)
        
        critical_high_sql = f"""
        SELECT COUNT(DISTINCT server_id) as count
        FROM mcp_risk_register
        WHERE risk_tier IN ('CRITICAL', 'HIGH')
          AND computed_at >= (CURRENT_TIMESTAMP - INTERVAL '{days} DAYS')
        """
        
        critical_high = ws_query(critical_high_sql)
        
        risk_percentage = 0
        if overall and overall[0].get("total_servers", 0) > 0:
            risk_percentage = round(100 * critical_high[0].get("count", 0) / overall[0].get("total_servers", 1), 2)
        
        recommendations = []
        if risk_percentage > 20:
            recommendations.append({
                "priority": "HIGH",
                "category": "risk_concentration",
                "message": f"{risk_percentage}% of servers are CRITICAL or HIGH risk. Immediate attention required."
            })
        
        high_critical = [d for d in distribution if d.get("risk_tier") in ("CRITICAL", "HIGH")]
        if high_critical:
            for tier in high_critical:
                avg_score = tier.get("avg_trust_score", 100)
                if avg_score and avg_score < 30:
                    recommendations.append({
                        "priority": "MEDIUM",
                        "category": "low_trust_score",
                        "message": f"Servers in {tier.get('risk_tier')} tier have low average trust score ({avg_score}). Consider review."
                    })
        
        if not recommendations:
            recommendations.append({
                "priority": "INFO",
                "category": "status",
                "message": "Risk tier distribution is within acceptable parameters."
            })
        
        return {
            "report_period_days": days,
            "generated_at": utc_now_iso(),
            "overall": overall[0] if overall else {},
            "distribution": distribution,
            "risk_percentage": risk_percentage,
            "recommendations": recommendations
        }
        
    except Exception as e:
        log.error(f"Error generating risk trend report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "generated_at": utc_now_iso()
    }


def run():
    import uvicorn
    uvicorn.run(router, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    run()