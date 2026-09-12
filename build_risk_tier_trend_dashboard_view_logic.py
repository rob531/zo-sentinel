import logging
import os
import sys
import time
import signal
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "risk_tier_trend_dashboard"
SERVICE_PORT = 8790
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
POLL_SECS = 3600
HEARTBEAT_INTERVAL = 300

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Service already running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            log.warning(f"Stale PID file {old_pid}, removing")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: list) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for {table}: {e}")
        return False


def ws_query(sql: str) -> list:
    payload = {"sql": sql}
    try:
        resp = requests.post(f"{QUERY_SERVICE_URL}/query", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        resp = requests.post(f"{EXECUTE_SERVICE_URL}/execute", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    ts = utc_now_iso()
    row = {
        "service": SERVICE_NAME,
        "status": status,
        "last_heartbeat": ts,
        "meta": meta
    }
    ws_write("service_health", [row])


def ensure_trend_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS risk_tier_trends (
        trend_id VARCHAR PRIMARY KEY,
        snapshot_time TIMESTAMPTZ,
        risk_tier VARCHAR,
        server_count INTEGER,
        percentage REAL,
        trend_direction VARCHAR,
        trend_magnitude REAL,
        computed_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)


def compute_trend_id(tier: str, snapshot_time: str) -> str:
    content = f"{tier}:{snapshot_time}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_risk_tier_distribution(snapshot_time: str) -> dict:
    sql = f"""
    SELECT 
        risk_tier,
        COUNT(*) as server_count
    FROM mcp_risk_register
    WHERE computed_at <= '{snapshot_time}'
    GROUP BY risk_tier
    ORDER BY risk_tier
    """
    return ws_query(sql)


def get_previous_distribution(hours_ago: int = 24) -> dict:
    sql = f"""
    SELECT risk_tier, server_count
    FROM risk_tier_trends
    WHERE snapshot_time >= NOW() - INTERVAL '{hours_ago} hours'
    ORDER BY snapshot_time DESC
    LIMIT 10
    """
    results = ws_query(sql)
    by_tier = {}
    for row in results:
        tier = row.get("risk_tier")
        if tier:
            by_tier[tier] = row.get("server_count", 0)
    return by_tier


def compute_total_servers() -> int:
    sql = "SELECT COUNT(*) as total FROM mcp_risk_register"
    results = ws_query(sql)
    if results:
        return results[0].get("total", 0)
    return 0


def determine_trend_direction(current: int, previous: int) -> str:
    if previous == 0:
        return "new"
    diff = current - previous
    if diff > 0:
        return "increasing"
    elif diff < 0:
        return "decreasing"
    return "stable"


def compute_trend_magnitude(current: int, previous: int) -> float:
    if previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 2)


def compute_risk_tier_trends() -> list:
    now = utc_now_iso()
    snapshot_time = now
    current_dist = get_risk_tier_distribution(snapshot_time)
    previous_dist = get_previous_distribution(hours_ago=24)
    total_servers = compute_total_servers()
    
    if not current_dist:
        log.warning("No risk tier distribution data available")
        return []
    
    trends = []
    for row in current_dist:
        tier = row.get("risk_tier", "unknown")
        current_count = row.get("server_count", 0)
        previous_count = previous_dist.get(tier, 0)
        percentage = (current_count / total_servers * 100) if total_servers > 0 else 0.0
        
        trend_direction = determine_trend_direction(current_count, previous_count)
        trend_magnitude = compute_trend_magnitude(current_count, previous_count)
        
        trend_id = compute_trend_id(tier, snapshot_time)
        
        trend_row = {
            "trend_id": trend_id,
            "snapshot_time": snapshot_time,
            "risk_tier": tier,
            "server_count": current_count,
            "percentage": round(percentage, 2),
            "trend_direction": trend_direction,
            "trend_magnitude": trend_magnitude,
            "computed_at": now
        }
        trends.append(trend_row)
    
    return trends


def write_trends_to_db(trends: list) -> None:
    if not trends:
        log.info("No trends to write")
        return
    
    for trend in trends:
        sql = f"""
        INSERT INTO risk_tier_trends 
        (trend_id, snapshot_time, risk_tier, server_count, percentage, trend_direction, trend_magnitude, computed_at)
        VALUES ('{trend['trend_id']}', '{trend['snapshot_time']}', '{trend['risk_tier']}', 
                {trend['server_count']}, {trend['percentage']}, '{trend['trend_direction']}', 
                {trend['trend_magnitude']}, '{trend['computed_at']}')
        ON CONFLICT (trend_id) DO UPDATE SET
            server_count = EXCLUDED.server_count,
            percentage = EXCLUDED.percentage,
            trend_direction = EXCLUDED.trend_direction,
            trend_magnitude = EXCLUDED.trend_magnitude,
            computed_at = EXCLUDED.computed_at
        """
        ws_execute(sql)
    
    log.info(f"Wrote {len(trends)} risk tier trend records")


def get_historical_trends(days: int = 7) -> list:
    sql = f"""
    SELECT 
        risk_tier,
        snapshot_time,
        server_count,
        percentage,
        trend_direction,
        trend_magnitude
    FROM risk_tier_trends
    WHERE snapshot_time >= NOW() - INTERVAL '{days} days'
    ORDER BY risk_tier, snapshot_time
    """
    return ws_query(sql)


def compute_tier_velocity(trends: list) -> dict:
    velocity = {}
    tier_data = {}
    
    for row in trends:
        tier = row.get("risk_tier")
        if tier not in tier_data:
            tier_data[tier] = []
        tier_data[tier].append({
            "time": row.get("snapshot_time"),
            "count": row.get("server_count", 0),
            "direction": row.get("trend_direction")
        })
    
    for tier, data_points in tier_data.items():
        if len(data_points) >= 2:
            oldest = data_points[0]
            newest = data_points[-1]
            delta = newest["count"] - oldest["count"]
            velocity[tier] = {
                "delta": delta,
                "start_count": oldest["count"],
                "end_count": newest["count"],
                "data_points": len(data_points)
            }
        else:
            velocity[tier] = {
                "delta": 0,
                "start_count": data_points[0]["count"] if data_points else 0,
                "end_count": data_points[0]["count"] if data_points else 0,
                "data_points": len(data_points)
            }
    
    return velocity


def cycle() -> dict:
    ensure_trend_table()
    
    trends = compute_risk_tier_trends()
    write_trends_to_db(trends)
    
    historical = get_historical_trends(days=7)
    velocity = compute_tier_velocity(historical)
    
    summary = {
        "timestamp": utc_now_iso(),
        "trends_computed": len(trends),
        "historical_points": len(historical),
        "velocity": velocity
    }
    
    return summary


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        sys.exit(1)
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    try:
        ensure_trend_table()
        send_heartbeat(status="starting", meta="initializing")
        
        while True:
            try:
                result = cycle()
                log.info(f"Cycle complete: {result['trends_computed']} trends, velocity={result['velocity']}")
                send_heartbeat(status="running", meta=f"trends:{result['trends_computed']}")
            except Exception as e:
                log.error(f"Cycle error: {e}", exc_info=True)
                send_heartbeat(status="error", meta=str(e))
            
            time.sleep(POLL_SECS)
    except Exception as e:
        log.error(f"Run loop error: {e}", exc_info=True)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()