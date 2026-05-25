import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "self_diagnostics_refresh"
SERVICE_PORT = None
WRITE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/home/workspace/zo_sentinel/{SERVICE_NAME}.pid"
POLL_SECS = 60
STALE_THRESHOLD_SECONDS = 600

logger = logging.getLogger(__name__)


def setup_logging():
    log_dir = Path("/home/workspace/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log"),
        ],
    )


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        if old_pid.isdigit():
            own_pid = os.getpid()
            if int(old_pid) != own_pid:
                import subprocess
                result = subprocess.run(
                    ["ps", "-p", old_pid, "-o", "pid="],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.error("Service already running with PID %s", old_pid)
                    sys.exit(1)
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down", signum)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> list:
    payload = {"table": "_internal", "sql": sql, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def find_stale_self_diagnostics() -> list:
    sql = f"""
    SELECT service_name, last_heartbeat
    FROM service_health
    WHERE service_name = 'self_diagnostics'
    AND last_heartbeat < (NOW() - INTERVAL '{STALE_THRESHOLD_SECONDS} seconds')::TIMESTAMP
    """
    try:
        result = ws_query(sql)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("Failed to query stale self_diagnostics: %s", e)
        return []


def send_fresh_heartbeat():
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "service_name": SERVICE_NAME,
            "status": "healthy",
            "last_heartbeat": now_iso,
            "meta": '{"action": "refresh_diagnostic_cycle"}',
        }
    ]
    try:
        ws_write("service_health", rows)
        logger.info("Fresh heartbeat written: %s", now_iso)
    except Exception as e:
        logger.error("Failed to write heartbeat: %s", e)


def cycle():
    logger.info("Checking for stale self_diagnostics entries...")
    stale_entries = find_stale_self_diagnostics()
    if stale_entries:
        logger.info("Found %d stale self_diagnostics entry(ies)", len(stale_entries))
        for entry in stale_entries:
            logger.info(
                "Stale entry: %s, last_heartbeat: %s",
                entry.get("service_name"),
                entry.get("last_heartbeat"),
            )
        send_fresh_heartbeat()
    else:
        logger.info("No stale self_diagnostics entries found")
        send_fresh_heartbeat()


def run():
    setup_logging()
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Starting %s daemon", SERVICE_NAME)
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error("Error in cycle: %s", e)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()