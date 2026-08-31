import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = "server_risk_timeline_api"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
WRITE_URL = "http://localhost:8772/write"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_write_url() -> str:
    return WRITE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            return False
        except (ProcessLookupError, ValueError):
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum: int, frame: Any) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {},
    }
    ws_write("service_health", [row])


def ensure_risk_timeline_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_risk_timeline (
        server_id VARCHAR PRIMARY KEY,
        timeline_data JSON,
        computed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)
    log.info("Ensured mcp_risk_timeline table exists")


def get_server_risk_history(server_id: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        r.server_id,
        r.risk_tier,
        r.risk_rank,
        r.threat_count,
        r.computed_at,
        s.trust_score,
        s.verdict,
        s.registry_source
    FROM mcp_risk_register r
    LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
    WHERE r.server_id = '{server_id}'
    ORDER BY r.computed_at DESC
    LIMIT 100
    """
    return ws_query(sql)


def get_all_server_ids_with_risk() -> List[str]:
    sql = """
    SELECT DISTINCT server_id 
    FROM mcp_risk_register 
    ORDER BY server_id
    """
    rows = ws_query(sql)
    return [r.get("server_id") for r in rows if r.get("server_id")]


def compute_risk_timeline(server_id: str) -> Dict[str, Any]:
    history = get_server_risk_history(server_id)
    
    if not history:
        return {
            "server_id": server_id,
            "has_history": False,
            "timeline": [],
            "risk_trend": "unknown",
            "current_risk_tier": None,
            "current_risk_rank": None,
        }
    
    timeline = []
    for record in history:
        timeline.append({
            "risk_tier": record.get("risk_tier"),
            "risk_rank": record.get("risk_rank"),
            "threat_count": record.get("threat_count"),
            "trust_score": record.get("trust_score"),
            "verdict": record.get("verdict"),
            "computed_at": record.get("computed_at"),
        })
    
    risk_tiers = [t.get("risk_tier") for t in timeline if t.get("risk_tier")]
    risk_trend = compute_risk_trend(risk_tiers)
    
    current = history[0] if history else {}
    
    return {
        "server_id": server_id,
        "has_history": True,
        "timeline": timeline,
        "risk_trend": risk_trend,
        "current_risk_tier": current.get("risk_tier"),
        "current_risk_rank": current.get("risk_rank"),
        "current_threat_count": current.get("threat_count"),
        "current_trust_score": current.get("trust_score"),
        "current_verdict": current.get("verdict"),
        "total_observations": len(timeline),
    }


def compute_risk_trend(risk_tiers: List[Optional[str]]) -> str:
    if not risk_tiers:
        return "unknown"
    
    risk_order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        "MINIMAL": 4,
    }
    
    numeric_values = []
    for tier in risk_tiers:
        if tier in risk_order:
            numeric_values.append(risk_order[tier])
    
    if len(numeric_values) < 2:
        return "stable"
    
    first_half_avg = sum(numeric_values[: len(numeric_values) // 2]) / (len(numeric_values) // 2)
    second_half_avg = sum(numeric_values[len(numeric_values) // 2 :]) / (len(numeric_values) - len(numeric_values) // 2)
    
    if second_half_avg < first_half_avg - 0.5:
        return "increasing"
    elif second_half_avg > first_half_avg + 0.5:
        return "decreasing"
    else:
        return "stable"


def compute_timeline_id(server_id: str) -> str:
    import hashlib
    content = f"risk_timeline_{server_id}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def persist_timeline(timeline_data: Dict[str, Any]) -> bool:
    server_id = timeline_data.get("server_id")
    if not server_id:
        return False
    
    timeline_json = str(timeline_data).replace("'", "''")
    
    sql = f"""
    INSERT INTO mcp_risk_timeline (server_id, timeline_data, computed_at)
    VALUES ('{server_id}', '{timeline_json}', '{utc_now_iso()}')
    ON CONFLICT (server_id) DO UPDATE SET
        timeline_data = EXCLUDED.timeline_data,
        computed_at = EXCLUDED.computed_at
    """
    return ws_execute(sql)


def compute_all_timelines() -> int:
    server_ids = get_all_server_ids_with_risk()
    log.info(f"Computing risk timelines for {len(server_ids)} servers")
    
    count = 0
    for server_id in server_ids:
        try:
            timeline_data = compute_risk_timeline(server_id)
            if timeline_data.get("has_history"):
                persist_timeline(timeline_data)
                count += 1
        except Exception as e:
            log.error(f"Failed to compute timeline for {server_id}: {e}")
            continue
    
    log.info(f"Computed and persisted {count} risk timelines")
    return count


def get_timeline_summary() -> Dict[str, Any]:
    sql = """
    SELECT 
        COUNT(*) as total_timelines,
        COUNT(CASE WHEN risk_trend = 'increasing' THEN 1 END) as increasing_trend,
        COUNT(CASE WHEN risk_trend = 'decreasing' THEN 1 END) as decreasing_trend,
        COUNT(CASE WHEN risk_trend = 'stable' THEN 1 END) as stable_trend,
        COUNT(CASE WHEN current_risk_tier = 'CRITICAL' THEN 1 END) as critical_count,
        COUNT(CASE WHEN current_risk_tier = 'HIGH' THEN 1 END) as high_count,
        COUNT(CASE WHEN current_risk_tier = 'MEDIUM' THEN 1 END) as medium_count,
        COUNT(CASE WHEN current_risk_tier = 'LOW' THEN 1 END) as low_count
    FROM (
        SELECT 
            server_id,
            timeline_data:$.risk_trend as risk_trend,
            timeline_data:$.current_risk_tier as current_risk_tier
        FROM mcp_risk_timeline
    )
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {}


def get_risk_timeline_by_server(server_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT server_id, timeline_data, computed_at
    FROM mcp_risk_timeline
    WHERE server_id = '{server_id}'
    """
    rows = ws_query(sql)
    if rows:
        row = rows[0]
        return {
            "server_id": row.get("server_id"),
            "timeline_data": row.get("timeline_data"),
            "computed_at": row.get("computed_at"),
        }
    
    timeline = compute_risk_timeline(server_id)
    if timeline.get("has_history"):
        persist_timeline(timeline)
        return timeline
    return None


def get_servers_by_risk_trend(trend: str, limit: int = 50) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        server_id,
        timeline_data,
        computed_at
    FROM mcp_risk_timeline
    WHERE timeline_data LIKE '%"{trend}"%'
    LIMIT {limit}
    """
    rows = ws_query(sql)
    results = []
    for row in rows:
        results.append({
            "server_id": row.get("server_id"),
            "timeline_data": row.get("timeline_data"),
            "computed_at": row.get("computed_at"),
        })
    return results


def cycle() -> None:
    log.info("Running risk timeline computation cycle")
    count = compute_all_timelines()
    summary = get_timeline_summary()
    log.info(f"Cycle complete. Summary: {summary}")
    send_heartbeat("running", {"servers_processed": count, "summary": summary})


def run() -> None:
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    if not check_single_instance():
        log.error("Single instance check failed")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        ensure_risk_timeline_table()
    except Exception as e:
        log.error(f"Failed to initialize tables: {e}")
    
    log.info(f"{SERVICE_NAME} initialized successfully")
    
    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Cycle failed: {e}")
            send_heartbeat("error", {"error": str(e)})
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()