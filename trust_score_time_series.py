#!/usr/bin/env python3
"""
trust_score_time_series.py -- ZO-SENTINEL Trust Score Time Series Tracker.
Monitors trust score drift over time, alerting on rapid changes that may indicate
score manipulation or compromise.
"""
import os
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
SERVICE_NAME = "trust_score_time_series"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8773"
HEARTBEAT_INTERVAL = 300
CYCLE_INTERVAL = 21600
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
REPORT_FILE = "DRIFT_REPORT.md"

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def get_execute_url() -> str:
    return EXECUTE_URL

def get_query_url() -> str:
    return QUERY_URL

def get_db_path() -> str:
    return os.environ.get("SENTINEL_DB_PATH", "/tmp/sentinel.duckdb")

def check_single_instance() -> bool:
    current_pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid != current_pid:
                try:
                    os.kill(old_pid, 0)
                    logger.warning(f"Another instance is running with PID {old_pid}")
                    return False
                except OSError:
                    logger.info("Stale PID file found, removing...")
                    os.remove(PID_FILE)
        except (ValueError, IOError):
            os.remove(PID_FILE)
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(current_pid))
    except IOError:
        logger.warning(f"Could not write PID file {PID_FILE}")
    return True

def ws_query(query: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            get_query_url(),
            json={"query": query},
            timeout=60
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: Any) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        response = requests.post(get_write_url(), json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed to {table}: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        response = requests.post(get_execute_url(), json={"sql": sql}, timeout=60)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return False

def send_heartbeat() -> None:
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()})

def get_current_trust_scores() -> List[Dict[str, Any]]:
    query = """
    SELECT server_id, name, trust_score, verdict, last_assessed
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    """
    return ws_query(query)

def get_trust_score_7_days_ago(server_id: str) -> Optional[Dict[str, Any]]:
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    query = f"""
    SELECT AVG(score) as historical_score, MAX(scored_at) as last_scored
    FROM mcp_signal_scores
    WHERE server_id = '{server_id.replace("'", "''")}'
    AND scored_at >= '{seven_days_ago}'::timestamp - INTERVAL '1 day'
    AND scored_at < '{seven_days_ago}'
    """
    results = ws_query(query)
    if results and results[0].get("historical_score") is not None:
        return results[0]
    return None

def get_trust_score_from_registry_history(server_id: str) -> Optional[float]:
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    query = f"""
    SELECT trust_score as historical_score
    FROM mcp_server_registry
    WHERE server_id = '{server_id.replace("'", "''")}'
    AND last_assessed < '{seven_days_ago}'
    ORDER BY last_assessed DESC
    LIMIT 1
    """
    results = ws_query(query)
    if results and results[0].get("historical_score") is not None:
        return results[0]["historical_score"]
    return None

def analyze_trust_drift(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    server_id = server["server_id"]
    current_score = server.get("trust_score")
    
    if current_score is None:
        return None
    
    historical_score = None
    
    signal_score_data = get_trust_score_7_days_ago(server_id)
    if signal_score_data and signal_score_data.get("historical_score") is not None:
        historical_score = signal_score_data["historical_score"]
    else:
        historical_score = get_trust_score_from_registry_history(server_id)
    
    if historical_score is None:
        logger.debug(f"No historical data for {server_id}, skipping")
        return None
    
    delta = current_score - historical_score
    
    alert_level = "INFO"
    alert_reason = "Normal drift within acceptable range"
    
    if delta > 20:
        alert_level = "WARNING"
        alert_reason = "Rapid improvement detected - possible score manipulation or artificial inflation"
    elif delta < -15:
        alert_level = "HIGH"
        alert_reason = "Significant degradation detected - possible compromise or suspicious activity"
    elif historical_score > 60 and current_score < 35:
        alert_level = "CRITICAL"
        alert_reason = "Trust score cliff drop detected - significant trust collapse requires immediate attention"
    
    return {
        "server_id": server_id,
        "server_name": server.get("name", "Unknown"),
        "current_score": round(current_score, 2),
        "historical_score": round(historical_score, 2),
        "delta": round(delta, 2),
        "alert_level": alert_level,
        "alert_reason": alert_reason,
        "current_verdict": server.get("verdict", "UNKNOWN"),
        "last_assessed": server.get("last_assessed")
    }

def record_threat_association(drift: Dict[str, Any]) -> bool:
    threat_payload = {
        "server_id": drift["server_id"],
        "threat_type": "TRUST_SCORE_CLIFF_DROP",
        "evidence": f"Trust score dropped from {drift['historical_score']} to {drift['current_score']} (delta: {drift['delta']}) within 7 days. Reason: {drift['alert_reason']}",
        "severity": "CRITICAL",
        "reported_at": datetime.utcnow().isoformat()
    }
    return ws_write("mcp_threat_associations", threat_payload)

def record_mesh_event(drift: Dict[str, Any]) -> bool:
    event_payload = {
        "event_type": "trust_score_drift",
        "payload": {
            "server_id": drift["server_id"],
            "server_name": drift.get("server_name", "Unknown"),
            "old_score": drift["historical_score"],
            "new_score": drift["current_score"],
            "delta": drift["delta"],
            "alert_level": drift["alert_level"],
            "alert_reason": drift["alert_reason"]
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    return ws_write("mesh_events", event_payload)

def write_drift_report(drift_results: List[Dict[str, Any]], report_time: datetime) -> None:
    sorted_results = sorted(drift_results, key=lambda x: abs(x["delta"]), reverse=True)
    
    critical_alerts = [d for d in sorted_results if d["alert_level"] == "CRITICAL"]
    high_alerts = [d for d in sorted_results if d["alert_level"] == "HIGH"]
    warning_alerts = [d for d in sorted_results if d["alert_level"] == "WARNING"]
    info_alerts = [d for d in sorted_results if d["alert_level"] == "INFO"]
    
    with open(REPORT_FILE, 'w') as f:
        f.write("# Trust Score Drift Report\n\n")
        f.write(f"**Generated:** {report_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n")
        f.write(f"**Analysis Period:** 7 days\n\n")
        f.write(f"**Total Servers Analyzed:** {len(sorted_results)}\n\n")
        f.write("---\n\n")
        f.write("## Summary\n\n")
        f.write(f"| Severity | Count |\n")
        f.write(f"|----------|-------|\n")
        f.write(f"| CRITICAL | {len(critical_alerts)} |\n")
        f.write(f"| HIGH     | {len(high_alerts)} |\n")
        f.write(f"| WARNING  | {len(warning_alerts)} |\n")
        f.write(f"| INFO     | {len(info_alerts)} |\n\n")
        
        if critical_alerts:
            f.write("---\n\n## CRITICAL Alerts (Trust Cliff Drop)\n\n")
            f.write("| Server ID | Server Name | Previous Score | Current Score | Delta |\n")
            f.write("|-----------|-------------|----------------|---------------|-------|\n")
            for d in critical_alerts:
                f.write(f"| {d['server_id']} | {d['server_name']} | {d['historical_score']} | {d['current_score']} | {d['delta']} |\n")
            f.write("\n")
        
        if high_alerts:
            f.write("---\n\n## HIGH Alerts (Significant Degradation)\n\n")
            f.write("| Server ID | Server Name | Previous Score | Current Score | Delta | Reason |\n")
            f.write("|-----------|-------------|----------------|---------------|-------|--------|\n")
            for d in high_alerts:
                f.write(f"| {d['server_id']} | {d['server_name']} | {d['historical_score']} | {d['current_score']} | {d['delta']} | {d['alert_reason']} |\n")
            f.write("\n")
        
        if warning_alerts:
            f.write("---\n\n## WARNING Alerts (Rapid Improvement)\n\n")
            f.write("| Server ID | Server Name | Previous Score | Current Score | Delta | Reason |\n")
            f.write("|-----------|-------------|----------------|---------------|-------|--------|\n")
            for d in warning_alerts:
                f.write(f"| {d['server_id']} | {d['server_name']} | {d['historical_score']} | {d['current_score']} | {d['delta']} | {d['alert_reason']} |\n")
            f.write("\n")
        
        if info_alerts:
            f.write("---\n\n## All Servers by Absolute Delta (Sorted)\n\n")
            f.write("| Rank | Server ID | Server Name | Previous | Current | Delta | Alert Level |\n")
            f.write("|------|-----------|-------------|----------|---------|-------|-------------|\n")
            for i, d in enumerate(sorted_results, 1):
                f.write(f"| {i} | {d['server_id']} | {d['server_name']} | {d['historical_score']} | {d['current_score']} | {d['delta']} | {d['alert_level']} |\n")
            f.write("\n")
        
        f.write("---\n\n")
        f.write("*This report is auto-generated by ZO-SENTINEL Trust Score Time Series Tracker*\n")
    
    logger.info(f"Drift report written to {REPORT_FILE}")

def run_cycle() -> None:
    logger.info("Starting trust score time series analysis cycle")
    report_time = datetime.utcnow()
    
    servers = get_current_trust_scores()
    logger.info(f"Found {len(servers)} servers with trust scores")
    
    drift_results = []
    critical_count = 0
    high_count = 0
    warning_count = 0
    
    for server in servers:
        drift = analyze_trust_drift(server)
        if drift:
            drift_results.append(drift)
            record_mesh_event(drift)
            
            if drift["alert_level"] == "CRITICAL":
                record_threat_association(drift)
                critical_count += 1
            elif drift["alert_level"] == "HIGH":
                high_count += 1
            elif drift["alert_level"] == "WARNING":
                warning_count += 1
    
    write_drift_report(drift_results, report_time)
    
    logger.info(f"Cycle complete: {len(drift_results)} servers analyzed, "
                f"CRITICAL={critical_count}, HIGH={high_count}, WARNING={warning_count}")
    send_heartbeat()

def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def run() -> None:
    if not check_single_instance():
        logger.error(f"Another instance of {SERVICE_NAME} is already running. Exiting.")
        return
    
    logger.info(f"Starting {SERVICE_NAME} daemon")
    logger.info(f"Cycle interval: {CYCLE_INTERVAL} seconds ({CYCLE_INTERVAL/3600:.1f} hours)")
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Error in cycle: {e}", exc_info=True)
        
        logger.info(f"Sleeping for {CYCLE_INTERVAL} seconds until next cycle")
        time.sleep(CYCLE_INTERVAL)

if __name__ == "__main__":
    run()