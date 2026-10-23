import os
import sys
import signal
import logging
from pathlib import Path
from datetime import datetime, timezone

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server_risk_tier_assignment_init.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "server_risk_tier_assignment_init"
SERVICE_PORT = 8795
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"

WRITE_TIMEOUT = 30
QUERY_TIMEOUT = 30


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ws_query(sql: str) -> list:
    import requests
    resp = requests.post(
        QUERY_SERVICE_URL,
        json={"sql": sql},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> dict:
    import requests
    resp = requests.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=WRITE_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    import requests
    resp = requests.post(
        EXECUTE_SERVICE_URL,
        json={"sql": sql},
        timeout=WRITE_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def check_single_instance() -> None:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance already running with PID %d", old_pid)
            sys.exit(1)
        except OSError:
            logger.warning("Stale PID file found (PID %d), removing", old_pid)
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum: int, frame) -> None:
    logger.info("Received signal %d, shutting down gracefully", signum)
    remove_pid_file()
    sys.exit(0)


def ensure_risk_tier_tables() -> None:
    logger.info("Ensuring risk tier tables exist")
    ws_execute("""
        CREATE TABLE IF NOT EXISTS server_risk_tier_assignment (
            server_id VARCHAR NOT NULL,
            risk_tier VARCHAR NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL,
            assigned_by VARCHAR,
            reason VARCHAR,
            metadata JSON,
            PRIMARY KEY (server_id)
        )
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS server_risk_tier_assignment_audit (
            assignment_id VARCHAR NOT NULL,
            server_id VARCHAR NOT NULL,
            old_tier VARCHAR,
            new_tier VARCHAR NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL,
            changed_by VARCHAR,
            reason VARCHAR,
            metadata JSON
        )
    """)
    logger.info("Risk tier tables verified")


def ensure_service_health() -> None:
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": "init",
        "meta": {"phase": "initialization"}
    }])


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    logger.info("Starting %s", SERVICE_NAME)

    try:
        ensure_risk_tier_tables()
        ensure_service_health()
        logger.info("%s initialization complete", SERVICE_NAME)
    except Exception as e:
        logger.error("Initialization failed: %s", e)
        remove_pid_file()
        sys.exit(1)

    remove_pid_file()


if __name__ == "__main__":
    run()