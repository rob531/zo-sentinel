#!/usr/bin/env python3
"""
decision_emitter_daemon.py
Long-running daemon that emits decisions for servers with attestations but no decisions.
Every 300s: query mcp_attestations for server_ids without mcp_decisions, apply thresholds,
write decisions to mcp_decisions table.
Heartbeat every 30s. Single-instance lockfile.
"""
import os
import sys
import time
import json
import threading
from datetime import datetime, timezone

SERVICE_NAME = "decision_emitter_daemon"
SERVICE_PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
LOCK_FILE = "/home/workspace/logs/decision_emitter_daemon.lock"
PID_FILE = "/home/workspace/logs/decision_emitter_daemon.pid"
CONFIG_FILE = "/home/workspace/zo_sentinel/decision_thresholds.json"

POLL_SECS = 300
HEARTBEAT_INTERVAL = 30

DEFAULT_THRESHOLDS = {
    "trusted": 70,
    "review": 40,
    "block": 0
}


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] {SERVICE_NAME}: {msg}", flush=True)


def ws_query(sql):
    try:
        import requests
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query error: {e}")
        return None


def ws_write(table, rows):
    try:
        import requests
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write error: {e}")
        return None


def check_single_instance():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, 'r') as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            log(f"Another instance running with PID {pid}. Exiting.")
            sys.exit(1)
        except OSError:
            log(f"Stale lockfile found (PID {pid} not running). Removing.")
            os.remove(LOCK_FILE)
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    log(f"Acquired lock. PID={os.getpid()}")


def remove_pid_file():
    for f in [LOCK_FILE, PID_FILE]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


def signal_handler(signum, frame):
    log(f"Received signal {signum}. Shutting down.")
    remove_pid_file()
    sys.exit(0)


def load_thresholds():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            return {
                "trusted": config.get("trusted_threshold", DEFAULT_THRESHOLDS["trusted"]),
                "review": config.get("review_threshold", DEFAULT_THRESHOLDS["review"]),
                "block": DEFAULT_THRESHOLDS["block"]
            }
        except Exception as e:
            log(f"Failed to load thresholds config: {e}. Using defaults.")
    return DEFAULT_THRESHOLDS


def get_servers_without_decisions(limit=100):
    sql = f"""
    SELECT DISTINCT a.server_id
    FROM mcp_attestations a
    LEFT JOIN mcp_decisions d ON a.server_id = d.server_id
    WHERE d.server_id IS NULL
    LIMIT {limit}
    """
    result = ws_query(sql)
    if result and result.get("rows"):
        return [row["server_id"] for row in result["rows"]]
    return []


def get_latest_signal_score(server_id):
    sql = f"""
    SELECT AVG(score) as avg_score, COUNT(*) as signal_count
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}'
    AND scored_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
    """
    result = ws_query(sql)
    if result and result.get("rows"):
        row = result["rows"][0]
        avg_score = row.get("avg_score")
        signal_count = row.get("signal_count", 0)
        if avg_score is not None:
            return float(avg_score), int(signal_count)
    return None, 0


def determine_verdict(score, thresholds):
    if score >= thresholds["trusted"]:
        return "trusted"
    elif score >= thresholds["review"]:
        return "review"
    else:
        return "block"


def emit_decision(server_id, verdict, score, signal_count):
    decided_at = datetime.now(timezone.utc).isoformat()
    rows = [{
        "server_id": server_id,
        "verdict": verdict,
        "score": score,
        "decided_at": decided_at,
        "basis_signal_count": signal_count
    }]
    result = ws_write("mcp_decisions", rows)
    if result and result.get("ok"):
        log(f"Decision emitted: {server_id} -> {verdict} (score={score:.2f}, signals={signal_count})")
        return True
    else:
        log(f"Failed to emit decision for {server_id}: {result}")
        return False


def send_heartbeat():
    try:
        import requests
        payload = {
            "table": "service_health",
            "rows": {"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()}
        }
        resp = requests.post(WRITE_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            log(f"Heartbeat sent at {datetime.now(timezone.utc).isoformat()}")
        else:
            log(f"Heartbeat failed: status {resp.status_code}")
    except Exception as e:
        log(f"Heartbeat error: {e}")


def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()


def ensure_mcp_decisions_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_decisions (
        server_id VARCHAR,
        verdict VARCHAR,
        score DOUBLE,
        decided_at VARCHAR,
        basis_signal_count INTEGER
    )
    """
    try:
        import requests
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        if resp.status_code in (200, 201):
            log("mcp_decisions table verified.")
        else:
            log(f"Table creation warning: {resp.status_code}")
    except Exception as e:
        log(f"Error ensuring mcp_decisions table: {e}")


def run():
    check_single_instance()
    ensure_mcp_decisions_table()
    thresholds = load_thresholds()
    log(f"Starting {SERVICE_NAME} with thresholds: trusted>={thresholds['trusted']}, review>={thresholds['review']}, block<{thresholds['review']}")

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    while True:
        try:
            server_ids = get_servers_without_decisions(100)
            if not server_ids:
                log("No servers without decisions found.")
            else:
                log(f"Found {len(server_ids)} servers needing decisions.")
                for server_id in server_ids:
                    score, signal_count = get_latest_signal_score(server_id)
                    if score is not None:
                        verdict = determine_verdict(score, thresholds)
                        emit_decision(server_id, verdict, score, signal_count)
                    else:
                        log(f"No recent signals for {server_id}. Skipping.")
        except Exception as e:
            log(f"Cycle error: {e}")

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    run()