import os
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
import uvicorn

from build_risk_tier_overview_router import ws_query, ws_write, ws_execute, WRITE_SERVICE_URL, QUERY_URL, EXECUTE_URL

SERVICE_NAME = "risk_tier_distribution_by_source_router"
PORT = 8786
app = FastAPI()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")]
)
log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60
last_heartbeat_ts = None


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    global last_heartbeat_ts
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "ts": utc_now_iso(),
        "last_heartbeat": last_heartbeat_ts
    }


def send_heartbeat():
    global last_heartbeat_ts
    ts = utc_now_iso()
    last_heartbeat_ts = ts
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": ts,
            "status": "running",
            "meta": "{}"
        }])
    except Exception as e:
        log.warning("Heartbeat failed: %s", e)


def get_risk_tier_distribution_by_source():
    sql = """
    SELECT
        r.registry_source,
        r.risk_tier,
        COUNT(*) as server_count
    FROM mcp_server_registry r
    WHERE r.risk_tier IS NOT NULL
      AND r.registry_source IS NOT NULL
    GROUP BY r.registry_source, r.risk_tier
    ORDER BY r.registry_source, r.risk_tier
    """
    result = ws_query(sql)
    return result if result else []


def get_source_summary():
    sql = """
    SELECT
        registry_source,
        COUNT(*) as total_servers,
        SUM(CASE WHEN risk_tier = 'CRITICAL' THEN 1 ELSE 0 END) as critical_count,
        SUM(CASE WHEN risk_tier = 'HIGH' THEN 1 ELSE 0 END) as high_count,
        SUM(CASE WHEN risk_tier = 'MEDIUM' THEN 1 ELSE 0 END) as medium_count,
        SUM(CASE WHEN risk_tier = 'LOW' THEN 1 ELSE 0 END) as low_count,
        SUM(CASE WHEN risk_tier = 'INFO' THEN 1 ELSE 0 END) as info_count,
        SUM(CASE WHEN risk_tier IS NULL OR risk_tier = '' THEN 1 ELSE 0 END) as unknown_count
    FROM mcp_server_registry
    WHERE registry_source IS NOT NULL
    GROUP BY registry_source
    ORDER BY total_servers DESC
    """
    result = ws_query(sql)
    return result if result else []


def get_verdict_distribution_by_source():
    sql = """
    SELECT
        registry_source,
        verdict,
        COUNT(*) as server_count
    FROM mcp_server_registry
    WHERE verdict IS NOT NULL
      AND registry_source IS NOT NULL
    GROUP BY registry_source, verdict
    ORDER BY registry_source, server_count DESC
    """
    result = ws_query(sql)
    return result if result else []


@app.get("/api/risk-tier-distribution-by-source")
def risk_tier_distribution_by_source():
    try:
        distribution = get_risk_tier_distribution_by_source()
        return {
            "ok": True,
            "data": distribution,
            "count": len(distribution),
            "ts": utc_now_iso()
        }
    except Exception as e:
        log.error("Failed to get risk tier distribution by source: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/source-summary")
def source_summary():
    try:
        summary = get_source_summary()
        return {
            "ok": True,
            "data": summary,
            "count": len(summary),
            "ts": utc_now_iso()
        }
    except Exception as e:
        log.error("Failed to get source summary: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/verdict-distribution-by-source")
def verdict_distribution_by_source():
    try:
        distribution = get_verdict_distribution_by_source()
        return {
            "ok": True,
            "data": distribution,
            "count": len(distribution),
            "ts": utc_now_iso()
        }
    except Exception as e:
        log.error("Failed to get verdict distribution by source: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/risk-tier-distribution-by-source/full")
def full_distribution():
    try:
        distribution = get_risk_tier_distribution_by_source()
        summary = get_source_summary()
        verdict_dist = get_verdict_distribution_by_source()
        return {
            "ok": True,
            "risk_tier_distribution": distribution,
            "source_summary": summary,
            "verdict_distribution": verdict_dist,
            "ts": utc_now_iso()
        }
    except Exception as e:
        log.error("Failed to get full distribution: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def run():
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()