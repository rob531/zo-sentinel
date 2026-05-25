import time
import json
import logging
import os
import signal
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = "mesh_sentinel_reporter"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
PORT = None
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
POLL_SECS = 21600
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            existing_pid = f.read().strip()
        if existing_pid and int(existing_pid) != pid:
            log.warning(f"Another instance running with PID {existing_pid}. Exiting.")
            return False
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    log.info(f"Running with PID {pid}")
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    exit(0)


def ws_query(sql: str) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"Query failed: {e} | SQL: {sql[:200]}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok"):
            return True
        log.warning(f"Write failed response: {result}")
        return False
    except requests.RequestException as e:
        log.error(f"Write failed: {e} | Table: {table}")
        return False


def send_heartbeat() -> bool:
    payload = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    }
    return ws_write("service_health", [payload])


def get_registry_stats() -> Dict[str, Any]:
    stats = {
        "total_servers": 0,
        "verdict_distribution": {},
        "avg_trust_score": 0.0,
        "registry_source_distribution": {}
    }
    
    result = ws_query("SELECT COUNT(*) as total FROM mcp_server_registry")
    if result and "rows" in result and result["rows"]:
        stats["total_servers"] = result["rows"][0].get("total", 0)
    
    result = ws_query("""
        SELECT verdict, COUNT(*) as count 
        FROM mcp_server_registry 
        WHERE verdict IS NOT NULL 
        GROUP BY verdict
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            stats["verdict_distribution"][row.get("verdict", "unknown")] = row.get("count", 0)
    
    result = ws_query("""
        SELECT AVG(trust_score) as avg_score 
        FROM mcp_server_registry 
        WHERE trust_score IS NOT NULL
    """)
    if result and "rows" in result and result["rows"]:
        avg_score = result["rows"][0].get("avg_score")
        if avg_score is not None:
            stats["avg_trust_score"] = round(float(avg_score), 3)
    
    result = ws_query("""
        SELECT registry_source, COUNT(*) as count 
        FROM mcp_server_registry 
        WHERE registry_source IS NOT NULL 
        GROUP BY registry_source
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            stats["registry_source_distribution"][row.get("registry_source", "unknown")] = row.get("count", 0)
    
    return stats


def get_threat_stats() -> Dict[str, Any]:
    stats = {
        "total_threats_7d": 0,
        "threats_by_severity": {},
        "threats_by_type": {}
    }
    
    cutoff_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    
    result = ws_query(f"""
        SELECT COUNT(*) as total 
        FROM mcp_threat_associations 
        WHERE reported_at >= '{cutoff_date}'
    """)
    if result and "rows" in result and result["rows"]:
        stats["total_threats_7d"] = result["rows"][0].get("total", 0)
    
    result = ws_query(f"""
        SELECT severity, COUNT(*) as count 
        FROM mcp_threat_associations 
        WHERE reported_at >= '{cutoff_date}' 
        GROUP BY severity
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            severity = row.get("severity") or "unknown"
            stats["threats_by_severity"][severity] = row.get("count", 0)
    
    result = ws_query(f"""
        SELECT threat_type, COUNT(*) as count 
        FROM mcp_threat_associations 
        WHERE reported_at >= '{cutoff_date}' 
        GROUP BY threat_type
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            threat_type = row.get("threat_type") or "unknown"
            stats["threats_by_type"][threat_type] = row.get("count", 0)
    
    return stats


def get_risk_register_stats() -> Dict[str, Any]:
    stats = {
        "total_registered": 0,
        "risk_tier_distribution": {},
        "high_risk_count": 0
    }
    
    result = ws_query("SELECT COUNT(*) as total FROM mcp_risk_register")
    if result and "rows" in result and result["rows"]:
        stats["total_registered"] = result["rows"][0].get("total", 0)
    
    result = ws_query("""
        SELECT risk_tier, COUNT(*) as count 
        FROM mcp_risk_register 
        WHERE risk_tier IS NOT NULL 
        GROUP BY risk_tier
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            tier = row.get("risk_tier") or "unknown"
            count = row.get("count", 0)
            stats["risk_tier_distribution"][tier] = count
            if tier.upper() in ("CRITICAL", "HIGH"):
                stats["high_risk_count"] += count
    
    return stats


def get_signal_stats() -> Dict[str, Any]:
    stats = {
        "total_signal_records": 0,
        "signal_types": {}
    }
    
    result = ws_query("SELECT COUNT(*) as total FROM mcp_signal_scores")
    if result and "rows" in result and result["rows"]:
        stats["total_signal_records"] = result["rows"][0].get("total", 0)
    
    result = ws_query("""
        SELECT signal_name, COUNT(*) as count 
        FROM mcp_signal_scores 
        GROUP BY signal_name
    """)
    if result and "rows" in result:
        for row in result["rows"]:
            signal_name = row.get("signal_name") or "unknown"
            stats["signal_types"][signal_name] = row.get("count", 0)
    
    return stats


def get_mesh_memory_stats() -> Dict[str, Any]:
    stats = {
        "total_entries": 0
    }
    
    result = ws_query("SELECT COUNT(*) as total FROM mesh_memory")
    if result and "rows" in result and result["rows"]:
        stats["total_entries"] = result["rows"][0].get("total", 0)
    
    return stats


def build_sentinel_stats() -> Dict[str, Any]:
    timestamp = datetime.utcnow().isoformat()
    
    registry_stats = get_registry_stats()
    threat_stats = get_threat_stats()
    risk_stats = get_risk_register_stats()
    signal_stats = get_signal_stats()
    mesh_stats = get_mesh_memory_stats()
    
    return {
        "timestamp": timestamp,
        "reporting_agent": SERVICE_NAME,
        "registry": registry_stats,
        "threats": threat_stats,
        "risk_register": risk_stats,
        "signals": signal_stats,
        "mesh_memory": mesh_stats,
        "reporting_period_days": 7
    }


def write_to_mesh_memory(stats: Dict[str, Any]) -> bool:
    content = json.dumps(stats, indent=2)
    entry = {
        "agent_id": "sentinel_reporter",
        "memory_type": "sentinel_stats",
        "content": content,
        "importance": 0.6
    }
    success = ws_write("mesh_memory", [entry])
    if success:
        log.info(f"Wrote sentinel stats to mesh_memory: {stats.get('timestamp')}")
    else:
        log.error("Failed to write sentinel stats to mesh_memory")
    return success


def cycle() -> bool:
    log.info("Starting sentinel stats collection cycle")
    
    try:
        stats = build_sentinel_stats()
        log.info(f"Collected stats: {stats['registry']['total_servers']} servers, "
                 f"{stats['threats']['total_threats_7d']} threats (7d), "
                 f"{stats['risk_register']['high_risk_count']} high-risk servers")
        
        write_to_mesh_memory(stats)
        send_heartbeat()
        
        log.info("Cycle completed successfully")
        return True
        
    except Exception as e:
        log.error(f"Cycle failed with error: {e}", exc_info=True)
        return False


def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log.error("Failed to acquire instance lock")
        return
    
    log.info(f"{SERVICE_NAME} starting. Poll interval: {POLL_SECS}s (6 hours)")
    
    try:
        while True:
            cycle()
            log.info(f"Sleeping for {POLL_SECS} seconds until next cycle")
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("Received keyboard interrupt")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()