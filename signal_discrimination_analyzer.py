#!/usr/bin/env python3
import time
import signal
import sys
from datetime import datetime
from fastapi import FastAPI
import uvicorn

SERVICE_NAME = "signal_discrimination_analyzer"
SERVICE_PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
START_TIME = time.time()
POLL_SECS = 300

app = FastAPI()

def check_single_instance():
    import os
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"[{SERVICE_NAME}] Instance already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(pid)

def remove_pid_file():
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    remove_pid_file()
    sys.exit(0)

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_SERVICE_URL

def ws_query(sql):
    import requests
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{SERVICE_NAME}] Query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(table, rows):
    import requests
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{SERVICE_NAME}] Write error: {e}")
        return {"ok": False}

def ws_execute(sql):
    import requests
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{SERVICE_NAME}] Execute error: {e}")
        return {"ok": False}

def send_heartbeat():
    now = datetime.utcnow().isoformat()
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": now})

def ensure_signal_quality_log_table():
    sql = """
    CREATE TABLE IF NOT EXISTS signal_quality_log (
        id INTEGER DEFAULT 0,
        signal_type VARCHAR,
        distinct_value_count INTEGER,
        is_weak BOOLEAN,
        min_score DOUBLE,
        max_score DOUBLE,
        avg_score DOUBLE,
        analyzed_at VARCHAR,
        UNIQUE(signal_type, analyzed_at)
    )
    """
    ws_execute(sql)

def get_signal_discrimination_stats():
    sql = """
    SELECT 
        signal_name,
        COUNT(DISTINCT server_id) as server_count,
        COUNT(*) as total_records,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        COUNT(DISTINCT score) as distinct_scores
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY signal_name
    """
    return ws_query(sql)

def analyze_discrimination_quality():
    result = get_signal_discrimination_stats()
    rows = result.get("rows", [])
    findings = []
    weak_signals = ["permission_scope", "temporal_stability", "tool_description_safety"]
    
    for row in rows:
        signal_name = row.get("signal_name", "")
        distinct_scores = row.get("distinct_scores", 0)
        is_weak = distinct_scores < 10
        min_score = row.get("min_score", 0)
        max_score = row.get("max_score", 0)
        avg_score = row.get("avg_score", 0)
        server_count = row.get("server_count", 0)
        
        finding = {
            "signal_type": signal_name,
            "distinct_value_count": distinct_scores,
            "is_weak": is_weak,
            "min_score": min_score,
            "max_score": max_score,
            "avg_score": avg_score,
            "analyzed_at": datetime.utcnow().isoformat()
        }
        findings.append(finding)
        
        if is_weak:
            print(f"[{SERVICE_NAME}] WEAK SIGNAL: {signal_name} has only {distinct_scores} distinct values (threshold: <10)")
            if signal_name in weak_signals:
                print(f"[{SERVICE_NAME}] TARGET WEAK SIGNAL DETECTED: {signal_name}")
        else:
            print(f"[{SERVICE_NAME}] Signal {signal_name}: {distinct_scores} distinct values - OK")
    
    if findings:
        ensure_signal_quality_log_table()
        for finding in findings:
            ws_write("signal_quality_log", finding)
        print(f"[{SERVICE_NAME}] Wrote {len(findings)} signal quality records")
    
    return findings

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_signal_quality_log_table()
    print(f"[{SERVICE_NAME}] Starting signal discrimination analyzer...")
    send_heartbeat()
    
    while True:
        try:
            findings = analyze_discrimination_quality()
            weak_count = sum(1 for f in findings if f["is_weak"])
            print(f"[{SERVICE_NAME}] Cycle complete. Weak signals: {weak_count}/{len(findings)}")
        except Exception as e:
            print(f"[{SERVICE_NAME}] Cycle error: {e}")
        
        send_heartbeat()
        time.sleep(POLL_SECS)

@app.get("/health")
def health():
    uptime = int(time.time() - START_TIME)
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}

@app.get("/analyze")
def analyze():
    findings = analyze_discrimination_quality()
    return {"findings": findings, "count": len(findings)}

if __name__ == "__main__":
    run()