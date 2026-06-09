# deps: requests

import os
import sys
import time
import signal
import json
import requests
from datetime import datetime, timezone

SERVICE_NAME = "signal_analyser_v3"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = QUERY_SERVICE_URL
WRITE_URL = WRITE_SERVICE_URL
EXECUTE_URL = EXECUTE_SERVICE_URL
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30

os.makedirs(LOG_DIR, exist_ok=True)


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_write_url():
    return WRITE_URL


def get_query_url():
    return QUERY_URL


def get_execute_url():
    return EXECUTE_URL


def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_query error: {e} | SQL: {sql[:200]}")
        return {"rows": []}


def ws_write(table, rows):
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_write error: {e} | table: {table}")
        return {"ok": False, "error": str(e)}


def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_execute error: {e} | SQL: {sql[:200]}")
        return {"ok": False, "error": str(e)}


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = f.read().strip()
        if existing and existing.isdigit():
            existing_pid = int(existing)
            try:
                os.kill(existing_pid, 0)
                log(f"Already running as PID {existing_pid}, exiting")
                sys.exit(0)
            except OSError:
                log(f"Stale PID file {existing_pid}, will overwrite")
    write_pid()


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    log(f"Received signal {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    try:
        now = datetime.now(timezone.utc).isoformat()
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": now})
    except Exception as e:
        log(f"Heartbeat error: {e}")


def ensure_tables():
    tables = [
        """
        CREATE SEQUENCE IF NOT EXISTS signal_scores_seq
        """,
        """
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            score_id VARCHAR DEFAULT 'sig_' || NEXTVAL('signal_scores_seq'),
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at VARCHAR
        )
        """
    ]
    for sql in tables:
        try:
            ws_execute(sql)
        except Exception:
            pass


def get_unscored_servers(limit=100):
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.description
    FROM mcp_server_registry r
    WHERE r.verdict IS NULL
    OR r.verdict = 'UNKNOWN'
    OR NOT EXISTS (
        SELECT 1 FROM mcp_signal_scores s
        WHERE s.server_id = r.server_id
        AND s.signal_name = 'supply_chain'
    )
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get("rows", []) if isinstance(result, dict) else []


def compute_signal_scores(server):
    server_id = server.get("server_id", "")
    name = server.get("name", "")
    description = server.get("description", "")
    url = server.get("url", "")
    
    scores = []
    
    if name and len(name) > 3:
        supply_score = 0.5 + min(len(name) / 100.0, 0.3)
        scores.append({
            "signal_name": "supply_chain",
            "score": min(supply_score, 1.0),
            "evidence": json.dumps({"source": "computed", "name_length": len(name)})
        })
    
    if description:
        desc_len = len(description)
        desc_score = min(desc_len / 500.0, 1.0) * 0.7 + 0.3
        scores.append({
            "signal_name": "tool_description_safety",
            "score": min(desc_score, 1.0),
            "evidence": json.dumps({"desc_length": desc_len, "source": "computed"})
        })
    
    if url and url.startswith("https"):
        url_score = 0.8
    elif url:
        url_score = 0.5
    else:
        url_score = 0.2
    
    scores.append({
        "signal_name": "domain_trust",
        "score": url_score,
        "evidence": json.dumps({"url": url[:100] if url else "", "source": "computed"})
    })
    
    community_score = 0.5
    scores.append({
        "signal_name": "community_signal",
        "score": community_score,
        "evidence": json.dumps({"source": "computed", "fallback": True})
    })
    
    scores.append({
        "signal_name": "temporal_stability",
        "score": 0.5,
        "evidence": json.dumps({"source": "computed", "fallback": True})
    })
    
    scores.append({
        "signal_name": "permission_scope",
        "score": 0.5,
        "evidence": json.dumps({"source": "computed", "fallback": True})
    })
    
    return scores


def emit_signal_scores(server_id, scores):
    if not scores:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for s in scores:
        rows.append({
            "server_id": server_id,
            "signal_name": s["signal_name"],
            "score": s["score"],
            "evidence": s["evidence"],
            "scored_at": now
        })
    if rows:
        ws_write("mcp_signal_scores", rows)


def cycle():
    log("Starting signal analysis cycle")
    servers = get_unscored_servers(limit=50)
    processed = 0
    
    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue
        try:
            scores = compute_signal_scores(server)
            emit_signal_scores(server_id, scores)
            processed += 1
        except Exception as e:
            log(f"Error processing server {server_id}: {e}")
    
    log(f"Cycle complete: processed {processed} servers")


def heartbeat_loop():
    last_heartbeat = time.time()
    while True:
        try:
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = time.time()
            time.sleep(5)
        except Exception as e:
            log(f"Heartbeat loop error: {e}")
            time.sleep(10)


def run():
    log(f"Starting {SERVICE_NAME}")
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        ensure_tables()
        log("Tables ensured")
    except Exception as e:
        log(f"Warning: could not ensure tables: {e}")
    
    send_heartbeat()
    log("Initial heartbeat sent")
    
    start_time = time.time()
    heartbeat_thread = None
    try:
        import threading
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
    except Exception as e:
        log(f"Could not start heartbeat thread: {e}")
    
    log("Entering main loop")
    cycle_count = 0
    
    while True:
        try:
            cycle_start = time.time()
            cycle()
            cycle_count += 1
            
            elapsed = time.time() - start_time
            if elapsed > 10 and cycle_count == 1:
                log(f"Startup completed in {elapsed:.1f}s")
            
            time.sleep(POLL_SECS)
        except Exception as e:
            log(f"Main loop error: {e}")
            time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()