import sys
import os
sys.path.insert(0, '/home/workspace')
from db_utils import ws_query, ws_write
import requests
import time
import signal
import json
from datetime import datetime

SERVICE_NAME = "aidr_verdict_check_wiring"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

VERDICT_BLOCK_THRESHOLDS = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"]

BLOCKING_VERDICTS = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"}
ALLOWED_VERDICTS = {"TRUSTED", "REVIEWED_SAFE", "LOW_RISK"}

LOG = []

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.append(line)

def signal_handler(sig, frame):
    log(f"Received signal {sig}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def remove_pid_file():
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
            log("Removed PID file")
        except Exception as e:
            log(f"Failed to remove PID file: {e}")

def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Another instance is running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    log(f"PID file created: {PID_FILE}")

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_SERVICE_URL

def get_execute_url():
    return EXECUTE_SERVICE_URL

def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql):
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])

def ws_execute(sql):
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat():
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}])
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def get_server_verdict(server_id):
    sql = f"SELECT verdict, risk_tier, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return None

def get_injection_resilience_score(server_id):
    sql = f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'"
    rows = ws_query(sql)
    if rows:
        return rows[0].get('score', 0.0)
    return 0.0

def get_signal_scores(server_id):
    sql = f"SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = '{server_id}'"
    return ws_query(sql)

def has_explicit_override(server_id, action="commit"):
    sql = f"SELECT id, decision, reason FROM mcp_decisions WHERE server_id = '{server_id}' AND action = '{action}' AND decision = 'APPROVED' AND (expires_at IS NULL OR expires_at > NOW())"
    rows = ws_query(sql)
    return len(rows) > 0

def get_composite_verdict(server_id):
    verdict_data = get_server_verdict(server_id)
    if verdict_data:
        return verdict_data.get('verdict', None)
    return None

def should_block_commit(server_id):
    verdict = get_composite_verdict(server_id)
    log(f"Server {server_id} verdict: {verdict}")
    if verdict in BLOCKING_VERDICTS:
        if has_explicit_override(server_id, "commit"):
            log(f"Server {server_id} has explicit override, allowing commit")
            return False, "Explicit override present"
        log(f"Server {server_id} verdict {verdict} requires blocking")
        return True, f"Verdict {verdict} blocked without override"
    return False, "Verdict allows commit"

def build_commit_payload(server_id, original_payload):
    injection_score = get_injection_resilience_score(server_id)
    signal_scores = get_signal_scores(server_id)
    payload = dict(original_payload)
    payload['injection_resilience_score'] = injection_score
    payload['all_signal_scores'] = signal_scores
    payload['verdict_check_timestamp'] = datetime.utcnow().isoformat()
    payload['verdict'] = get_composite_verdict(server_id)
    return payload

def check_mcp_decisions_table():
    sql = "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_decisions'"
    rows = ws_query(sql)
    if not rows:
        log("Creating mcp_decisions table")
        create_sql = """
        CREATE TABLE IF NOT EXISTS mcp_decisions (
            id INTEGER PRIMARY KEY,
            server_id TEXT NOT NULL,
            action TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            override_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT
        )
        """
        ws_execute(create_sql)
        log("mcp_decisions table created")

def verdict_check_gate(server_id, original_payload):
    log(f"Verdict check gate for server: {server_id}")
    block, reason = should_block_commit(server_id)
    if block:
        log(f"COMMIT BLOCKED for {server_id}: {reason}")
        return {"allowed": False, "reason": reason, "server_id": server_id}
    payload = build_commit_payload(server_id, original_payload)
    log(f"COMMIT ALLOWED for {server_id}: {payload.get('verdict')}")
    return {"allowed": True, "payload": payload, "reason": reason}

def ensure_tables():
    check_mcp_decisions_table()

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_tables()
    log(f"{SERVICE_NAME} started on port {SERVICE_PORT}")
    cycle_count = 0
    while True:
        try:
            send_heartbeat()
            cycle_count += 1
            if cycle_count % 60 == 0:
                log(f"Heartbeat cycle {cycle_count}")
            time.sleep(60)
        except Exception as e:
            log(f"Error in cycle: {e}")
            time.sleep(10)

if __name__ == '__main__':
    run()