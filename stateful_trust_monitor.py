#!/usr/bin/env python3
"""Stateful Trust Monitor - Detects trust score drift on MCP servers."""

import time
import requests
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

POLL_SECS = 30
DRIFT_THRESHOLD = 15
SERVICE_NAME = "stateful_trust_monitor"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
ALERT_MANAGER_URL = "http://127.0.0.1:8776/alert"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

start_time = time.time()


def check_single_instance():
    """Ensure only one instance runs."""
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Instance already running with PID {old_pid}")
            exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def send_heartbeat():
    """Send service heartbeat to write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def query_servers() -> List[Dict[str, Any]]:
    """Fetch all servers with their current trust scores."""
    sql = """
        SELECT 
            server_id,
            name,
            trust_score,
            verdict
        FROM mcp_server_registry
        WHERE trust_score IS NOT NULL
    """
    try:
        response = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        data = response.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"Failed to query servers: {e}")
        return []


def get_baseline_scores() -> Dict[str, float]:
    """Fetch stored baseline trust scores from mcp_risk_register."""
    sql = """
        SELECT 
            server_id,
            risk_rank as baseline_score
        FROM mcp_risk_register
    """
    try:
        response = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        data = response.json()
        return {row["server_id"]: float(row["baseline_score"]) for row in data.get("rows", [])}
    except Exception as e:
        logger.error(f"Failed to fetch baselines: {e}")
        return {}


def log_trust_drift(server_id: str, server_name: str, baseline: float, current: float, drift: float):
    """Log trust drift event to audit_log."""
    sql = f"""
        INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at)
        VALUES (
            '{server_id}',
            'TRUST_DRIFT_DETECTED',
            'stateful_trust_monitor',
            '{{"server_name": "{server_name}", "baseline_score": {baseline}, "current_score": {current}, "drift": {drift}}}',
            '{datetime.utcnow().isoformat()}'
        )
    """
    try:
        response = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=10)
        if response.ok:
            logger.info(f"Logged drift for server {server_id}: {drift} points")
    except Exception as e:
        logger.error(f"Failed to log drift: {e}")


def update_baseline(server_id: str, current_score: float):
    """Update baseline in mcp_risk_register after drift is handled."""
    sql = f"""
        UPDATE mcp_risk_register
        SET risk_rank = {current_score},
            computed_at = '{datetime.utcnow().isoformat()}'
        WHERE server_id = '{server_id}'
    """
    try:
        requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=10)
    except Exception as e:
        logger.error(f"Failed to update baseline: {e}")


def fire_alert(server_id: str, server_name: str, drift: float):
    """Fire alert via alert_manager."""
    alert_payload = {
        "alert_type": "TRUST_DRIFT",
        "severity": "HIGH",
        "source": "stateful_trust_monitor",
        "target_server_id": server_id,
        "message": f"Trust drift detected for {server_name}: {drift} points",
        "details": {
            "drift_threshold": DRIFT_THRESHOLD,
            "actual_drift": drift
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        response = requests.post(ALERT_MANAGER_URL, json=alert_payload, timeout=10)
        if response.ok:
            logger.info(f"Alert fired for server {server_id}")
        else:
            logger.warning(f"Alert manager returned: {response.status_code}")
    except Exception as e:
        logger.error(f"Failed to fire alert: {e}")


def initialize_baselines():
    """Initialize baseline scores for all servers if not present."""
    servers = query_servers()
    for server in servers:
        server_id = server["server_id"]
        current_score = server["trust_score"]
        check_sql = f"SELECT COUNT(*) as cnt FROM mcp_risk_register WHERE server_id = '{server_id}'"
        try:
            response = requests.post(QUERY_SERVICE_URL, json={"sql": check_sql}, timeout=10)
            data = response.json()
            rows = data.get("rows", [])
            if rows and rows[0]["cnt"] == 0:
                insert_sql = f"""
                    INSERT INTO mcp_risk_register (server_id, risk_tier, risk_rank, threat_count, computed_at)
                    VALUES (
                        '{server_id}',
                        'MEDIUM',
                        {current_score},
                        0,
                        '{datetime.utcnow().isoformat()}'
                    )
                """
                requests.post(EXECUTE_SERVICE_URL, json={"sql": insert_sql}, timeout=10)
                logger.info(f"Initialized baseline for server {server_id}")
        except Exception as e:
            logger.error(f"Failed to initialize baseline for {server_id}: {e}")


def check_trust_drift():
    """Main drift detection logic."""
    servers = query_servers()
    baselines = get_baseline_scores()
    
    for server in servers:
        server_id = server["server_id"]
        server_name = server["name"]
        current_score = server["trust_score"]
        
        if server_id not in baselines:
            continue
        
        baseline = baselines[server_id]
        drift = abs(current_score - baseline)
        
        if drift > DRIFT_THRESHOLD:
            logger.warning(
                f"Trust drift detected for {server_name} (ID: {server_id}): "
                f"baseline={baseline}, current={current_score}, drift={drift}"
            )
            log_trust_drift(server_id, server_name, baseline, current_score, drift)
            fire_alert(server_id, server_name, drift)
            update_baseline(server_id, current_score)
        else:
            update_baseline(server_id, current_score)


def run():
    """Main run loop."""
    check_single_instance()
    logger.info(f"Starting {SERVICE_NAME}")
    
    initialize_baselines()
    send_heartbeat()
    
    while True:
        try:
            check_trust_drift()
            send_heartbeat()
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
        
        time.sleep(POLL_SECS)


def get_uptime() -> float:
    """Calculate uptime in seconds."""
    return time.time() - start_time


# FastAPI health endpoint
try:
    from fastapi import FastAPI
    import uvicorn
    
    app = FastAPI()
    
    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "uptime": get_uptime()
        }
    
    def run_api():
        uvicorn.run(app, host="127.0.0.1", port=8785)
    
except ImportError:
    logger.warning("FastAPI not available, health endpoint disabled")
    app = None


if __name__ == "__main__":
    if app is not None:
        import threading
        api_thread = threading.Thread(target=run_api, daemon=True)
        api_thread.start()
    
    run()