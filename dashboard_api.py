#!/usr/bin/env python3
"""
dashboard_api.py -- ZO-SENTINEL Dashboard Data API
FastAPI service on port 8783 providing dashboard metrics and trends.
All DB reads via write_service query endpoint on port 8772.
"""
import logging
import requests
import uvicorn
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = 'dashboard_api'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
WRITE_URL = 'http://127.0.0.1:8772/write'
PORT = 8783
HEARTBEAT_INTERVAL = 60

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = FastAPI(title="ZO-SENTINEL Dashboard API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def ws_query(sql: str, params=None) -> list:
    """Execute SQL query against DuckDB via write_service execute endpoint."""
    try:
        payload = {'sql': sql}
        if params:
            payload['params'] = params
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get('rows', [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
        return []


def ws_write(table: str, rows, wait: bool = True) -> dict:
    """Write rows to DuckDB via write_service."""
    try:
        payload = {'table': table, 'rows': rows, 'wait': wait}
        resp = requests.post(WRITE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return {}


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'running'
        })
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


def get_summary() -> dict:
    """Build dashboard summary from various tables."""
    summary = {
        'total_servers': 0,
        'verdict_breakdown': {},
        'risk_tier_breakdown': {},
        'avg_trust_score': None,
        'last_scan_time': None,
        'pipeline_health': {}
    }
    
    total_result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if total_result:
        summary['total_servers'] = total_result[0].get('cnt', 0)
    
    verdict_result = ws_query("""
        SELECT verdict, COUNT(*) as cnt 
        FROM mcp_server_registry 
        WHERE verdict IS NOT NULL 
        GROUP BY verdict
    """)
    for row in verdict_result:
        summary['verdict_breakdown'][row.get('verdict', 'UNKNOWN')] = row.get('cnt', 0)
    
    risk_result = ws_query("""
        SELECT risk_tier, COUNT(*) as cnt 
        FROM mcp_server_registry 
        WHERE risk_tier IS NOT NULL 
        GROUP BY risk_tier
    """)
    for row in risk_result:
        summary['risk_tier_breakdown'][row.get('risk_tier', 'UNKNOWN')] = row.get('cnt', 0)
    
    trust_result = ws_query("SELECT AVG(trust_score) as avg_score FROM mcp_server_registry WHERE trust_score IS NOT NULL")
    if trust_result:
        summary['avg_trust_score'] = trust_result[0].get('avg_score')
    
    last_scan_result = ws_query("SELECT MAX(last_seen) as last_scan FROM mcp_server_registry")
    if last_scan_result:
        summary['last_scan_time'] = last_scan_result[0].get('last_scan')
    
    health_result = ws_query("""
        SELECT status, COUNT(*) as cnt 
        FROM service_health 
        GROUP BY status
    """)
    for row in health_result:
        summary['pipeline_health'][row.get('status', 'UNKNOWN')] = row.get('cnt', 0)
    
    return summary


def get_recent_events(limit: int = 20) -> list:
    """Fetch last N build events from mesh_events."""
    result = ws_query(f"""
        SELECT id, event_type, server_id, details, created_at 
        FROM mesh_events 
        ORDER BY created_at DESC NULLS LAST 
        LIMIT {limit}
    """)
    return result


def get_top_risks(limit: int = 10) -> list:
    """Fetch top servers by risk_rank from mcp_risk_register JOIN mcp_server_registry."""
    result = ws_query(f"""
        SELECT 
            r.server_id,
            r.risk_rank,
            r.risk_score,
            r.risk_factors,
            r.recommendation,
            s.name,
            s.url,
            s.verdict,
            s.trust_score
        FROM mcp_risk_register r
        JOIN mcp_server_registry s ON r.server_id = s.server_id
        ORDER BY r.risk_rank ASC NULLS LAST
        LIMIT {limit}
    """)
    return result


def get_trends(days: int = 7) -> list:
    """Fetch verdict distribution over last N days grouped by date."""
    result = ws_query(f"""
        SELECT 
            DATE(last_assessed) as date,
            verdict,
            COUNT(*) as count
        FROM mcp_server_registry
        WHERE last_assessed >= NOW() - INTERVAL '{days} days'
          AND verdict IS NOT NULL
        GROUP BY DATE(last_assessed), verdict
        ORDER BY DATE(last_assessed) DESC, verdict
    """)
    return result


@app.get("/health")
def health():
    """Service health check."""
    send_heartbeat()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "port": PORT,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/dashboard/summary")
def dashboard_summary():
    """Dashboard summary with total servers, verdict breakdown, risk tier breakdown, avg trust score, last scan time, pipeline health counts."""
    try:
        summary = get_summary()
        return {
            "status": "ok",
            "data": summary,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        log.error(f"Summary error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/dashboard/recent")
def dashboard_recent(limit: int = 20):
    """Last N build events from mesh_events."""
    try:
        events = get_recent_events(limit=limit)
        return {
            "status": "ok",
            "data": events,
            "count": len(events),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        log.error(f"Recent events error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/dashboard/top_risks")
def dashboard_top_risks(limit: int = 10):
    """Top N servers by risk_rank from mcp_risk_register JOIN mcp_server_registry."""
    try:
        risks = get_top_risks(limit=limit)
        return {
            "status": "ok",
            "data": risks,
            "count": len(risks),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        log.error(f"Top risks error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/dashboard/trends")
def dashboard_trends(days: int = 7):
    """Verdict distribution over last N days grouped by date."""
    try:
        trends = get_trends(days=days)
        return {
            "status": "ok",
            "data": trends,
            "days": days,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        log.error(f"Trends error: {e}")
        return {"status": "error", "message": str(e)}


def run():
    """Start the dashboard API server."""
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == '__main__':
    run()