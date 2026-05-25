#!/usr/bin/env python3
"""
metrics_exporter.py -- Prometheus-compatible metrics exporter for ZO-SENTINEL.
Exposes metrics in Prometheus text format at GET /metrics on port 8789.
Background task updates metrics every 60 seconds.
"""
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from threading import Lock

import requests
from fastapi import FastAPI, Response
import uvicorn

# Configuration
SERVICE_NAME = "metrics_exporter"
PORT = 8789
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Thread-safe metrics storage
metrics_lock = Lock()
metrics_data: Dict[str, Any] = {
    "servers_total": {},
    "trust_score_avg": 0.0,
    "threats_total": {},
    "assessments_24h": 0,
    "pipeline_health": {},
    "api_latency_ms": {},
}
last_update: Optional[datetime] = None


def ws_query(query: str, params: Optional[Dict[str, Any]] = None) -> list:
    """Query the inference router."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"query": query, "params": params or {}},
            timeout=15
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", result.get("data", []))
    except Exception as e:
        logger.debug(f"Query error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        logger.debug(f"Write error: {e}")
        return False


def send_heartbeat() -> bool:
    """Send service heartbeat to write_service."""
    try:
        return ws_write(
            "service_health",
            {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat(),
                "status": "running"
            }
        )
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def fetch_server_counts() -> Dict[str, int]:
    """Fetch server counts by verdict."""
    counts = {"unknown": 0, "approved": 0, "pending": 0, "rejected": 0, "flagged": 0}
    try:
        result = ws_query("SELECT verdict, COUNT(*) as cnt FROM mcp_server_registry GROUP BY verdict")
        for row in result:
            verdict = row.get("verdict", "unknown") or "unknown"
            if verdict in counts:
                counts[verdict] = row.get("cnt", 0)
            else:
                counts[verdict] = row.get("cnt", 0)
    except Exception as e:
        logger.debug(f"Server counts error: {e}")
    return counts


def fetch_trust_score_avg() -> float:
    """Calculate average trust score across all servers."""
    try:
        result = ws_query("SELECT AVG(trust_score) as avg_score FROM mcp_server_registry WHERE trust_score IS NOT NULL")
        if result and len(result) > 0:
            avg = result[0].get("avg_score")
            return float(avg) if avg is not None else 0.0
    except Exception as e:
        logger.debug(f"Trust score avg error: {e}")
    return 0.0


def fetch_threats_by_severity() -> Dict[str, int]:
    """Fetch threat counts by severity."""
    threats = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    try:
        result = ws_query("""
            SELECT severity, COUNT(*) as cnt 
            FROM mcp_threat_associations 
            GROUP BY severity
        """)
        for row in result:
            sev = row.get("severity", "unknown")
            if sev in threats:
                threats[sev] = row.get("cnt", 0)
    except Exception as e:
        logger.debug(f"Threats error: {e}")
    return threats


def fetch_assessments_24h() -> int:
    """Count assessments in the last 24 hours."""
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        result = ws_query("""
            SELECT COUNT(*) as cnt 
            FROM mcp_server_registry 
            WHERE last_assessed >= ?
        """, {"p1": cutoff.isoformat()})
        if result and len(result) > 0:
            return result[0].get("cnt", 0)
    except Exception as e:
        logger.debug(f"Assessments 24h error: {e}")
    return 0


def fetch_pipeline_health() -> Dict[str, int]:
    """Check health of various pipeline components."""
    health = {
        "write_service": 0,
        "query_service": 0,
        "registry": 0,
        "scanner": 0
    }
    
    # Check write_service
    try:
        resp = requests.get("http://127.0.0.1:8772/health", timeout=3)
        if resp.status_code == 200:
            health["write_service"] = 1
    except:
        pass
    
    # Check query_service
    try:
        resp = requests.get("http://127.0.0.1:8773/health", timeout=3)
        if resp.status_code == 200:
            health["query_service"] = 1
    except:
        pass
    
    # Check registry table
    try:
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
        if result:
            health["registry"] = 1
    except:
        pass
    
    # Check scanner daemon via supervisor
    try:
        resp = requests.get("http://127.0.0.1:9001/api/state", timeout=3)
        if resp.status_code == 200:
            state = resp.json()
            if "mcp_scanner" in state.get("active", []):
                health["scanner"] = 1
    except:
        # Fallback: check if scanner has recent heartbeat
        try:
            result = ws_query("""
                SELECT last_heartbeat FROM service_health 
                WHERE service = 'mcp_scanner'
                ORDER BY last_heartbeat DESC LIMIT 1
            """)
            if result:
                last_hb = datetime.fromisoformat(result[0].get("last_heartbeat", "2000-01-01"))
                if (datetime.utcnow() - last_hb).seconds < 120:
                    health["scanner"] = 1
        except:
            pass
    
    return health


def measure_api_latency(service_url: str, service_name: str) -> float:
    """Measure API latency in milliseconds."""
    try:
        start = time.time()
        response = requests.get(service_url, timeout=5)
        elapsed = (time.time() - start) * 1000
        if response.status_code == 200:
            return elapsed
    except:
        pass
    return 0.0


def update_all_metrics() -> None:
    """Fetch all metrics and update the internal store."""
    global metrics_data, last_update
    
    with metrics_lock:
        logger.info("Updating metrics...")
        
        # Fetch server counts
        metrics_data["servers_total"] = fetch_server_counts()
        
        # Fetch average trust score
        metrics_data["trust_score_avg"] = fetch_trust_score_avg()
        
        # Fetch threats by severity
        metrics_data["threats_total"] = fetch_threats_by_severity()
        
        # Fetch assessments 24h
        metrics_data["assessments_24h"] = fetch_assessments_24h()
        
        # Fetch pipeline health
        metrics_data["pipeline_health"] = fetch_pipeline_health()
        
        # Measure API latencies
        latencies = {}
        for service, url in [
            ("write_service", "http://127.0.0.1:8772/health"),
            ("query_service", "http://127.0.0.1:8773/health"),
            ("dashboard_api", "http://127.0.0.1:8788/health"),
        ]:
            latency = measure_api_latency(url, service)
            if latency > 0:
                latencies[service] = latency
        metrics_data["api_latency_ms"] = latencies
        
        last_update = datetime.utcnow()
        logger.info("Metrics updated successfully")


def format_prometheus_metrics() -> str:
    """Format metrics in Prometheus text format."""
    with metrics_lock:
        lines = [
            "# HELP zo_sentinel_servers_total Total number of MCP servers by verdict",
            "# TYPE zo_sentinel_servers_total gauge",
        ]
        
        for verdict, count in metrics_data["servers_total"].items():
            verdict_label = verdict.lower() if verdict else "unknown"
            lines.append(f'zo_sentinel_servers_total{{verdict="{verdict_label}"}} {count}')
        
        lines.extend([
            "",
            "# HELP zo_sentinel_trust_score_avg Average trust score across all servers",
            "# TYPE zo_sentinel_trust_score_avg gauge",
            f"zo_sentinel_trust_score_avg {metrics_data['trust_score_avg']:.4f}",
            "",
            "# HELP zo_sentinel_threats_total Total threats by severity",
            "# TYPE zo_sentinel_threats_total counter",
        ])
        
        for severity, count in metrics_data["threats_total"].items():
            lines.append(f'zo_sentinel_threats_total{{severity="{severity}"}} {count}')
        
        lines.extend([
            "",
            "# HELP zo_sentinel_assessments_24h Number of assessments in the last 24 hours",
            "# TYPE zo_sentinel_assessments_24h counter",
            f"zo_sentinel_assessments_24h {metrics_data['assessments_24h']}",
            "",
            "# HELP zo_sentinel_pipeline_health Pipeline component health status (1=ok, 0=degraded)",
            "# TYPE zo_sentinel_pipeline_health gauge",
        ])
        
        for check, status in metrics_data["pipeline_health"].items():
            lines.append(f'zo_sentinel_pipeline_health{{check="{check}"}} {status}')
        
        lines.extend([
            "",
            "# HELP zo_sentinel_api_latency_ms API request latency in milliseconds",
            "# TYPE zo_sentinel_api_latency_ms gauge",
        ])
        
        for service, latency in metrics_data["api_latency_ms"].items():
            lines.append(f'zo_sentinel_api_latency_ms{{service="{service}"}} {latency:.2f}')
        
        # Add process metrics
        lines.extend([
            "",
            "# HELP zo_sentinel_metrics_uptime_seconds Seconds since last metrics update",
            "# TYPE zo_sentinel_metrics_uptime_seconds gauge",
        ])
        
        if last_update:
            uptime = (datetime.utcnow() - last_update).total_seconds()
            lines.append(f"zo_sentinel_metrics_uptime_seconds {uptime:.2f}")
        else:
            lines.append("zo_sentinel_metrics_uptime_seconds 0")
        
        return "\n".join(lines) + "\n"


def run_metrics_update_loop() -> None:
    """Background loop to update metrics every 60 seconds."""
    while True:
        try:
            update_all_metrics()
            send_heartbeat()
        except Exception as e:
            logger.error(f"Metrics update error: {e}")
        
        time.sleep(HEARTBEAT_INTERVAL)


def start_background_tasks() -> None:
    """Start background metric update thread."""
    import threading
    thread = threading.Thread(target=run_metrics_update_loop, daemon=True)
    thread.start()
    logger.info("Background metrics update thread started")


# FastAPI App
app = FastAPI(title="ZO-SENTINEL Metrics Exporter")


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    content = format_prometheus_metrics()
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "port": PORT,
        "last_update": last_update.isoformat() if last_update else None
    }


def run():
    """Run the metrics exporter service."""
    logger.info(f"Starting {SERVICE_NAME} on port {PORT}")
    
    # Start background metrics collection
    start_background_tasks()
    
    # Run FastAPI with uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )


if __name__ == "__main__":
    run()