#!/usr/bin/env python3
"""Diagnostic tool for signal_analyser - checks for stale/inactive signal analyser."""
import os
import signal
import sys
import time
from datetime import datetime

SERVICE_NAME = "signal_analyser"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"

def check_pid_file():
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (ValueError, IOError):
        return None

def is_process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def get_process_uptime(pid):
    try:
        stat_path = f"/proc/{pid}/stat"
        with open(stat_path) as f:
            parts = f.read().split()
            start_time = float(parts[21])
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
        return uptime_seconds - start_time
    except (ValueError, IOError, IndexError):
        return None

def read_stderr(pid):
    try:
        fd_path = f"/proc/{pid}/fd/2"
        target = os.readlink(fd_path)
        if target and target != "/dev/null" and not target.endswith("pipe:"):
            with open(target) as f:
                content = f.read()
                return content[-2000:] if len(content) > 2000 else content
    except (IOError, OSError):
        pass
    return None

def get_last_heartbeat():
    import requests
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": f"SELECT last_heartbeat FROM service_health WHERE service = '{SERVICE_NAME}'"},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows") and len(data["rows"]) > 0:
                return data["rows"][0].get("last_heartbeat")
    except Exception:
        pass
    return None

def diagnose_stale_signal_analyser():
    print(f"=== {SERVICE_NAME} Diagnostic ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    pid = check_pid_file()
    if pid is None:
        print("❌ PID file not found")
        return False
    
    print(f"📄 PID file: {PID_FILE}")
    print(f"📌 PID: {pid}")
    
    if not is_process_running(pid):
        print("❌ Process not running (stale PID)")
        return False
    
    print("✅ Process is running")
    
    uptime = get_process_uptime(pid)
    if uptime:
        print(f"⏱️  Uptime: {uptime:.0f} seconds ({uptime/3600:.1f} hours)")
    
    heartbeat = get_last_heartbeat()
    if heartbeat:
        print(f"💓 Last heartbeat: {heartbeat}")
        if isinstance(heartbeat, str):
            try:
                hb_time = datetime.fromisoformat(heartbeat.replace('Z', '+00:00'))
                age_seconds = (datetime.now() - hb_time.replace(tzinfo=None)).total_seconds()
                print(f"⏰ Heartbeat age: {age_seconds:.0f} seconds ({age_seconds/60:.1f} minutes)")
                if age_seconds > 600:
                    print("⚠️  WARNING: Heartbeat is stale (>10 minutes)")
            except ValueError:
                pass
    else:
        print("⚠️  No heartbeat recorded in service_health")
    
    stderr_content = read_stderr(pid)
    if stderr_content:
        print("\n📋 Recent stderr output:")
        print("-" * 40)
        for line in stderr_content.strip().split('\n')[-20:]:
            print(f"  {line}")
        print("-" * 40)
    else:
        print("📋 No stderr capture available")
    
    return True

def run():
    is_running = diagnose_stale_signal_analyser()
    sys.exit(0 if is_running else 1)

if __name__ == "__main__":
    run()