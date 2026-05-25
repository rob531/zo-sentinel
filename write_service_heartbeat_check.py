import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import requests

SERVICE_NAME = "write_service_heartbeat_check"
SERVICE_PORT = None
PID_FILE = "/home/workspace/zo_sentinel/write_service_heartbeat_check.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
STALE_THRESHOLD_SECS = 300

logger = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    payload = {"table": "_internal_query", "rows": {"sql": sql}, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "ok":
        return data.get("rows", [])
    return []


def send_heartbeat(status: str, meta: str = ""):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": ts,
        "meta": meta,
    }
    try:
        requests.post(
            WRITE_SERVICE_URL,
            json={"table": "service_health", "rows": row, "wait": True},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Failed to send own heartbeat: %s", e)


def check_single_instance():
    pid_str = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        if old_pid and old_pid != pid_str:
            try:
                os.kill(int(old_pid), 0)
                logger.error("Already running as PID %s, exiting.", old_pid)
                sys.exit(1)
            except OSError:
                pass
    with open(PID_FILE, "w") as f:
        f.write(pid_str)


def remove_pid_file():
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


def signal_handler(signum, frame):
    sig_name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(signum, signum)
    logger.info("Received %s, shutting down.", sig_name)
    remove_pid_file()
    sys.exit(0)


def cycle():
    sql = "SELECT service_name, last_heartbeat FROM service_health WHERE service_name = 'write_service' ORDER BY last_heartbeat DESC LIMIT 1"
    rows = ws_query(sql)

    now_ts = datetime.now(timezone.utc)
    report = {
        "checked_at": now_ts.isoformat(),
        "threshold_secs": STALE_THRESHOLD_SECS,
        "write_service_found": False,
        "last_heartbeat": None,
        "age_secs": None,
        "is_stale": None,
        "diagnostic": None,
    }

    if not rows:
        report["diagnostic"] = "NO_ROW: write_service has no entry in service_health. WriteService may be down or not yet started."
        logger.warning("diagnostic=%s", report["diagnostic"])
        send_heartbeat("warn", report["diagnostic"])
        return

    row = rows[0]
    report["write_service_found"] = True
    lhb = row.get("last_heartbeat") or row.get("last_heartbeat")
    report["last_heartbeat"] = lhb

    if not lhb:
        report["diagnostic"] = "NULL_HEARTBEAT: write_service row exists but last_heartbeat is NULL."
        logger.warning("diagnostic=%s", report["diagnostic"])
        send_heartbeat("warn", report["diagnostic"])
        return

    try:
        hb_ts = datetime.fromisoformat(lhb.replace("Z", "+00:00"))
        age = (now_ts - hb_ts).total_seconds()
        report["age_secs"] = round(age, 1)
    except Exception as e:
        report["diagnostic"] = f"PARSE_ERROR: Could not parse heartbeat '{lhb}': {e}"
        logger.error("diagnostic=%s", report["diagnostic"])
        send_heartbeat("error", report["diagnostic"])
        return

    is_stale = age > STALE_THRESHOLD_SECS
    report["is_stale"] = is_stale

    if is_stale:
        age_m = round(age / 60, 1)
        report["diagnostic"] = f"STALE: write_service heartbeat is {age_m}min old (threshold={STALE_THRESHOLD_SECS}s). WriteService may be wedged."
        logger.warning("diagnostic=%s", report["diagnostic"])
        send_heartbeat("warn", report["diagnostic"])
    else:
        report["diagnostic"] = f"HEALTHY: write_service heartbeat is {age:.0f}s old."
        logger.info("diagnostic=%s", report["diagnostic"])
        send_heartbeat("ok", report["diagnostic"])

    logger.info("Heartbeat report: %s", report)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Starting %s (PID=%s)", SERVICE_NAME, os.getpid())
    poll_secs = 60
    while True:
        cycle()
        time.sleep(poll_secs)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")],
    )
    run()