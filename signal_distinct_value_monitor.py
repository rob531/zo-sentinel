#!/usr/bin/env python3
"""Signal Distinct Value Monitor - ZO-SENTINEL

Monitors trust framework signal quality by tracking distinct-value counts.
Emits WARNING mesh_events when signals have < 5 distinct values.
Writes per-signal counts to mesh_memory for trend visibility.
"""

import time
import httpx
import os
import signal
import sys
from datetime import datetime, timezone
from fastapi import FastAPI
import uvicorn

SERVICE_NAME = "signal_distinct_value_monitor"
POLL_SECS = 1800
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE = "http://127.0.0.1:8772"
START_TIME = time.time()

app = FastAPI()

def check_single_instance():
    pid = os.getpid()
    try:
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                print(f"[{SERVICE_NAME}] Instance already running (PID {old_pid}). Exiting.")
                sys.exit(0)
            except OSError:
                pass
    except FileNotFoundError:
        pass
    except ValueError:
        pass
    
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    print(f"[{SERVICE_NAME}] Started PID {pid}")

def cleanup_pid_file(signum, frame):
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    sys.exit(0)

def send_heartbeat():
    try:
        httpx.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "service_health",
                "rows": [{
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat()
                }]
            },
            timeout=10
        )
    except Exception as e:
        print(f"[{SERVICE_NAME}] Heartbeat failed: {e}")

def get_all_signal_names():
    """Query distinct signal_names from mcp_signal_scores."""
    try:
        resp = httpx.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": "SELECT DISTINCT signal_name FROM mcp_signal_scores"},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            return [row["signal_name"] for row in data.get("rows", [])]
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to get signal names: {e}")
    return []

def count_distinct_values(signal_name):
    """Count distinct values for a given signal across the population."""
    try:
        resp = httpx.post(
            f"{WRITE_SERVICE}/query",
            json={
                "sql": f"SELECT COUNT(DISTINCT score) as distinct_count FROM mcp_signal_scores WHERE signal_name = '{signal_name}'"
            },
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows"):
                return data["rows"][0].get("distinct_count", 0)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to count distinct for {signal_name}: {e}")
    return 0

def emit_warning_event(signal_name, distinct_count, timestamp):
    """Write WARNING mesh_event when signal has < 5 distinct values."""
    try:
        httpx.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "mesh_events",
                "rows": [{
                    "event_type": "signal_low_diversity",
                    "severity": "WARNING",
                    "signal_name": signal_name,
                    "distinct_value_count": distinct_count,
                    "threshold": 5,
                    "detail": f"Signal '{signal_name}' has only {distinct_count} distinct score values across population (expected >= 5). This may indicate low signal variance or measurement issues.",
                    "created_at": timestamp
                }]
            },
            timeout=15
        )
        print(f"[{SERVICE_NAME}] EMITTED WARNING for signal '{signal_name}': distinct_count={distinct_count}")
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to emit warning event: {e}")

def write_to_mesh_memory(signal_name, distinct_count, total_servers, timestamp):
    """Persist per-signal distinct counts to mesh_memory for trend tracking."""
    try:
        httpx.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "mesh_memory",
                "rows": [{
                    "memory_type": "signal_distinct_value",
                    "signal_name": signal_name,
                    "distinct_value_count": distinct_count,
                    "total_servers": total_servers,
                    "recorded_at": timestamp
                }]
            },
            timeout=15
        )
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to write mesh_memory for {signal_name}: {e}")

def get_total_server_count():
    """Get total server count for context."""
    try:
        resp = httpx.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": "SELECT COUNT(DISTINCT server_id) as total FROM mcp_signal_scores"},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows"):
                return data["rows"][0].get("total", 0)
    except Exception:
        pass
    return 0

def run_monitoring_cycle():
    """Execute one monitoring cycle: check all signals, emit warnings, store memory."""
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{SERVICE_NAME}] Starting monitoring cycle at {timestamp}")
    
    signal_names = get_all_signal_names()
    if not signal_names:
        print(f"[{SERVICE_NAME}] No signals found in mcp_signal_scores table")
        return
    
    print(f"[{SERVICE_NAME}] Found {len(signal_names)} distinct signals")
    total_servers = get_total_server_count()
    
    warnings_emitted = 0
    for signal_name in sorted(signal_names):
        distinct_count = count_distinct_values(signal_name)
        print(f"[{SERVICE_NAME}] Signal '{signal_name}': {distinct_count} distinct values")
        
        write_to_mesh_memory(signal_name, distinct_count, total_servers, timestamp)
        
        if distinct_count > 0 and distinct_count < 5:
            emit_warning_event(signal_name, distinct_count, timestamp)
            warnings_emitted += 1
    
    print(f"[{SERVICE_NAME}] Cycle complete. Warnings emitted: {warnings_emitted}")

def daemon_loop():
    """Main daemon loop with polling."""
    print(f"[{SERVICE_NAME}] Entering daemon loop (poll interval: {POLL_SECS}s)")
    
    while True:
        try:
            run_monitoring_cycle()
            send_heartbeat()
        except Exception as e:
            print(f"[{SERVICE_NAME}] Cycle error: {e}")
        
        time.sleep(POLL_SECS)

@app.get("/health")
def health_check():
    """Health endpoint for the monitor."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": int(time.time() - START_TIME)
    }

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, cleanup_pid_file)
    signal.signal(signal.SIGINT, cleanup_pid_file)
    
    print(f"[{SERVICE_NAME}] Running monitoring daemon on port 8788")
    uvicorn.run(app, host="127.0.0.1", port=8788, log_level="warning")

if __name__ == "__main__":
    run()
    daemon_loop()