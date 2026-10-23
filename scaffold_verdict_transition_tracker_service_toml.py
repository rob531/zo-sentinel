import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "verdict_transition_tracker"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def ws_query(sql: str) -> list:
    payload = {"sql": sql}
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    result = response.json()
    return result.get("rows", [])


def ws_execute(sql: str) -> dict:
    payload = {"sql": sql}
    response = requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance is running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ProcessLookupError):
            log.warning(f"Stale PID file found: {old_pid}")
            pid_file.unlink()


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.error(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: dict = None):
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {}
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning(f"Failed to send heartbeat: {e}")


def ensure_tables():
    create_verdict_transition_sql = """
    CREATE TABLE IF NOT EXISTS verdict_transitions (
        transition_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        from_verdict VARCHAR,
        to_verdict VARCHAR NOT NULL,
        transition_type VARCHAR NOT NULL,
        triggered_by VARCHAR,
        evidence JSON,
        transitioned_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    create_idx_sql = """
    CREATE INDEX IF NOT EXISTS idx_verdict_transitions_server 
    ON verdict_transitions(server_id)
    """
    try:
        ws_execute(create_verdict_transition_sql)
        ws_execute(create_idx_sql)
        log.info("Tables ensured for verdict transition tracker")
    except Exception as e:
        log.error(f"Failed to ensure tables: {e}")
        raise


def compute_transition_id(server_id: str, new_verdict: str, ts: str) -> str:
    import hashlib
    content = f"{server_id}:{new_verdict}:{ts}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def detect_transitions():
    sql = f"""
    SELECT 
        server_id,
        name,
        verdict,
        last_seen
    FROM mcp_server_registry
    WHERE last_seen >= NOW() - INTERVAL '1 hour'
    ORDER BY last_seen DESC
    LIMIT 100
    """
    servers = ws_query(sql)
    transitions = []
    for server in servers:
        server_id = server.get("server_id")
        verdict = server.get("verdict")
        if not server_id or not verdict:
            continue
        transitions.append({
            "transition_id": compute_transition_id(server_id, verdict, utc_now_iso()),
            "server_id": server_id,
            "from_verdict": None,
            "to_verdict": verdict,
            "transition_type": "snapshot",
            "triggered_by": "scheduled_scan",
            "evidence": {"name": server.get("name"), "trust_score": server.get("trust_score")},
            "transitioned_at": utc_now_iso()
        })
    return transitions


def cycle():
    log.info("Running verdict transition detection cycle")
    try:
        transitions = detect_transitions()
        if transitions:
            ws_write("verdict_transitions", transitions)
            log.info(f"Recorded {len(transitions)} verdict transitions")
        else:
            log.info("No new verdict transitions detected")
    except Exception as e:
        log.error(f"Error in transition detection cycle: {e}")


def run():
    import signal
    import time

    log.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        ensure_tables()
    except Exception as e:
        log.error(f"Failed to initialize tables: {e}")
        remove_pid_file()
        sys.exit(1)

    POLL_SECS = 300
    while True:
        try:
            cycle()
            send_heartbeat(status="running", meta={"last_cycle": utc_now_iso()})
        except Exception as e:
            log.error(f"Error in main loop: {e}")
            send_heartbeat(status="error", meta={"error": str(e)})
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()