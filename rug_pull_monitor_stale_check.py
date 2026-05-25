#!/usr/bin/env python3
"""
rug_pull_monitor_stale_check.py -- Diagnostic module for rug_pull_monitor heartbeat staleness.
Queries service_health for heartbeat age, inspects daemon source for heartbeat loop,
checks supervisord config, and outputs restart recommendation.
One-shot diagnostic; exits 0 on completion.
"""
import os
import sys
import ast
import requests
import logging
from datetime import datetime, timezone

SERVICE_NAME = "rug_pull_monitor_stale_check"
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_PATH = "/home/workspace/logs/rug_pull_monitor_stale_check.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

STALE_THRESHOLD_SECS = 28800  # 8 hours
DAEMON_PATH = "/home/workspace/zo_sentinel/rug_pull_monitor.py"
SUPERVISORD_CONF = "/etc/supervisord.d/supervisord_sentinel_full.conf"


def ws_query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_service_health():
    """Query service_health for rug_pull_monitor last heartbeat."""
    sql = """
    SELECT last_heartbeat, status, meta
    FROM service_health
    WHERE service = 'rug_pull_monitor'
    ORDER BY ts DESC
    LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    if not rows:
        logger.warning("No service_health entry found for rug_pull_monitor")
        return None, None
    row = rows[0]
    last_hb = row.get("last_heartbeat") or row.get("ts")
    status = row.get("status", "unknown")
    meta = row.get("meta", "{}")
    return last_hb, status, meta


def compute_staleness(last_hb_iso):
    """Compute seconds since last heartbeat."""
    if not last_hb_iso:
        return float('inf')
    try:
        if last_hb_iso.endswith('Z'):
            last_hb_iso = last_hb_iso[:-1]
        last_dt = datetime.fromisoformat(last_hb_iso.replace('+00:00', ''))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - last_dt).total_seconds()
    except Exception as e:
        logger.error("Failed to parse last_heartbeat '%s': %s", last_hb_iso, e)
        return float('inf')


def inspect_daemon_source():
    """Parse rug_pull_monitor.py AST to verify heartbeat loop exists."""
    if not os.path.exists(DAEMON_PATH):
        logger.error("Daemon source not found at %s", DAEMON_PATH)
        return False, "source_not_found"
    try:
        with open(DAEMON_PATH, "r") as f:
            source = f.read()
        tree = ast.parse(source)
        # Check for send_heartbeat function definition
        has_send_hb = any(
            isinstance(n, ast.FunctionDef) and n.name == "send_heartbeat"
            for n in ast.walk(tree)
        )
        # Check for heartbeat in run() or cycle() loop
        has_hb_call = "send_heartbeat" in source
        # Check for heartbeat in service_health table write
        has_service_health = "service_health" in source
        return has_send_hb and has_hb_call and has_service_health, "heartbeat_loop_found" if (has_send_hb and has_hb_call) else "no_heartbeat_loop"
    except Exception as e:
        logger.error("Failed to parse daemon source: %s", e)
        return False, "parse_error"


def check_supervisord_config():
    """Check if rug_pull_monitor is configured in supervisord."""
    if not os.path.exists(SUPERVISORD_CONF):
        logger.warning("Supervisord config not found at %s", SUPERVISORD_CONF)
        return False
    try:
        with open(SUPERVISORD_CONF, "r") as f:
            content = f.read()
        # Look for [program:rug_pull_monitor] section
        in_section = False
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('[') and 'rug_pull_monitor' in line:
                in_section = True
            elif line.startswith('['):
                in_section = False
            if in_section and ('command=' in line or 'autorestart=' in line):
                return True
        return False
    except Exception as e:
        logger.error("Failed to read supervisord config: %s", e)
        return False


def format_duration(seconds):
    """Format seconds as human-readable duration."""
    if seconds == float('inf'):
        return "forever (no heartbeat)"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h{minutes}m"


def main():
    logger.info("=== rug_pull_monitor staleness diagnostic ===")

    last_hb, status, meta = check_service_health()
    staleness_secs = compute_staleness(last_hb)
    is_stale = staleness_secs > STALE_THRESHOLD_SECS

    daemon_ok, daemon_msg = inspect_daemon_source()
    in_supervisord = check_supervisord_config()

    logger.info("last_heartbeat: %s", last_hb or "NONE")
    logger.info("staleness: %s (threshold: %ds)", format_duration(staleness_secs), STALE_THRESHOLD_SECS)
    logger.info("daemon_source_has_heartbeat: %s", daemon_ok)
    logger.info("in_supervisord: %s", in_supervisord)

    print("\n=== DIAGNOSTIC REPORT ===")
    print(f"Last heartbeat:  {last_hb or 'NONE (no row in service_health)'}")
    print(f"Staleness:       {format_duration(staleness_secs)} ({staleness_secs:.0f}s)")
    print(f"Threshold:       {STALE_THRESHOLD_SECS}s (8 hours)")
    print(f"Status:          {'STALE' if is_stale else 'OK'}")
    print(f"Daemon has heartbeat loop: {'YES' if daemon_ok else 'NO'}")
    print(f"Supervisord entry: {'YES' if in_supervisord else 'NO'}")

    if is_stale:
        print("\n=== RECOMMENDATION: RESTART REQUIRED ===")
        print("The rug_pull_monitor daemon has not sent a heartbeat in", format_duration(staleness_secs))
        print("Expected heartbeat interval: 21600s (6 hours)")
        print("Stale threshold exceeded:", format_duration(staleness_secs), ">", f"{STALE_THRESHOLD_SECS}s")
        print("\nManual restart commands:")
        print("  sudo supervisorctl stop rug_pull_monitor")
        print("  sudo supervisorctl start rug_pull_monitor")
        print("  # OR via the relaunch wrapper:")
        print("  nohup python3 /home/workspace/logs/_relaunch_rug_pull_monitor.py &")
    else:
        print("\n=== RECOMMENDATION: NO ACTION NEEDED ===")
        print("Heartbeat is fresh. Staleness is within acceptable bounds.")

    logger.info("Diagnostic complete. Exit 0.")
    sys.exit(0)


if __name__ == "__main__":
    main()