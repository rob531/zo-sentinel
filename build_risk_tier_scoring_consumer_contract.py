import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "risk_tier_scoring_consumer"
SERVICE_PORT = 8791
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    payload = {"sql": sql}
    resp = requests.post(
        f"{QUERY_SERVICE_URL}/query", json=payload, timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_execute(sql: str) -> dict:
    payload = {"sql": sql}
    resp = requests.post(
        f"{EXECUTE_SERVICE_URL}/execute", json=payload, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found for PID {old_pid}")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink()
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": status,
        "ts": utc_now_iso(),
        "meta": meta,
    }])


def compute_transition_id(server_id: str, from_tier: str, to_tier: str, ts: str) -> str:
    import hashlib
    raw = f"{server_id}:{from_tier}:{to_tier}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def compute_deterministic_id(*fields: str) -> str:
    import hashlib
    raw = "|".join(fields)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_risk_tier_from_score(trust_score: float) -> str:
    if trust_score >= 85:
        return "LOW"
    elif trust_score >= 70:
        return "MEDIUM"
    elif trust_score >= 50:
        return "HIGH"
    else:
        return "CRITICAL"


def ensure_tables() -> None:
    ws_execute("""
        CREATE TABLE IF NOT EXISTS risk_tier_scoring_events (
            event_id VARCHAR PRIMARY KEY,
            server_id VARCHAR NOT NULL,
            previous_tier VARCHAR,
            new_tier VARCHAR NOT NULL,
            trust_score DOUBLE,
            trigger_source VARCHAR,
            triggered_at TIMESTAMPTZ,
            processed_at TIMESTAMPTZ,
            meta JSON
        )
    """)


def get_pending_tier_changes() -> list:
    sql = """
        SELECT 
            server_id,
            trust_score,
            verdict,
            last_scored,
            previous_tier
        FROM mcp_server_registry r
        LEFT JOIN (
            SELECT server_id as sid, MAX(scored_at) as last_scored
            FROM mcp_signal_scores
            GROUP BY server_id
        ) s ON r.server_id = s.sid
        WHERE trust_score IS NOT NULL
        ORDER BY last_scored DESC NULLS LAST
        LIMIT 100
    """
    return ws_query(sql)


def compute_current_tier(server: dict) -> str:
    trust_score = server.get("trust_score", 0.0)
    return get_risk_tier_from_score(trust_score)


def record_tier_change(server_id: str, previous_tier: str, new_tier: str, server: dict) -> None:
    if previous_tier == new_tier:
        return
    
    event_id = compute_transition_id(
        server_id,
        previous_tier or "NONE",
        new_tier,
        utc_now_iso()
    )
    
    ws_write("risk_tier_scoring_events", [{
        "event_id": event_id,
        "server_id": server_id,
        "previous_tier": previous_tier,
        "new_tier": new_tier,
        "trust_score": server.get("trust_score"),
        "trigger_source": "risk_tier_scoring_consumer",
        "triggered_at": utc_now_iso(),
        "processed_at": utc_now_iso(),
        "meta": {},
    }])


def process_server(server: dict) -> None:
    server_id = server.get("server_id")
    if not server_id:
        return
    
    current_tier = compute_current_tier(server)
    previous_tier = server.get("previous_tier")
    
    record_tier_change(server_id, previous_tier, current_tier, server)


def cycle() -> int:
    pending = get_pending_tier_changes()
    processed = 0
    
    for server in pending:
        try:
            process_server(server)
            processed += 1
        except Exception as e:
            log.error(f"Failed to process server {server.get('server_id')}: {e}")
    
    return processed


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    send_heartbeat("started")
    
    log.info(f"{SERVICE_NAME} running on PID {os.getpid()}")
    
    while True:
        try:
            processed = cycle()
            if processed > 0:
                log.info(f"Cycle complete: processed {processed} tier changes")
            send_heartbeat("running", f"processed={processed}")
        except Exception as e:
            log.error(f"Cycle error: {e}")
            send_heartbeat("error", str(e))
        
        time.sleep(60)


if __name__ == "__main__":
    run()