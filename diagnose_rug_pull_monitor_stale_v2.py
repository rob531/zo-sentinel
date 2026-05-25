#!/usr/bin/env python3
"""Diagnostic for rug_pull_monitor being stale for 140h+."""

import os
import time
import json
from datetime import datetime

SERVICE_NAME = "rug_pull_monitor"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/zo_sentinel/logs/{SERVICE_NAME}.log"
SUPERVISOR_CONF = "/etc/supervisor/conf.d/rug_pull_monitor.conf"

def check_supervisord_entry():
    """Check if supervisord.conf entry exists and is enabled."""
    result = {"exists": False, "enabled": False, "raw": ""}
    if os.path.exists(SUPERVISOR_CONF):
        result["exists"] = True
        with open(SUPERVOR_CONF, 'r') as f:
            content = f.read()
            result["raw"] = content
        # Check for enabled/disabled flags
        lines = content.lower().split('\n')
        for line in lines:
            if 'autostart=true' in line or 'enabled=true' in line:
                result["enabled"] = True
            if 'autostart=false' in line or 'enabled=false' in line or 'comment' in line:
                result["enabled"] = False
                break
    return result

def check_pid_file():
    """Check if PID file exists and if process is running."""
    result = {"exists": False, "stale": False, "pid": None, "running": False, "age_seconds": None}
    if os.path.exists(PID_FILE):
        result["exists"] = True
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
                result["pid"] = pid
            # Check if process is running
            try:
                os.kill(pid, 0)
                result["running"] = True
            except OSError:
                result["stale"] = True
                # Get file age
                mtime = os.path.getmtime(PID_FILE)
                result["age_seconds"] = time.time() - mtime
        except (ValueError, IOError) as e:
            result["error"] = str(e)
    return result

def check_service_health():
    """Get last heartbeat from service_health table via write_service."""
    import requests
    result = {"found": False, "last_heartbeat": None, "age_seconds": None, "age_human": None}
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": f"SELECT last_heartbeat FROM service_health WHERE service='{SERVICE_NAME}'"},
            timeout=5
        )
        data = resp.json()
        if data.get("rows") and len(data["rows"]) > 0:
            result["found"] = True
            ts = data["rows"][0][0]
            result["last_heartbeat"] = ts
            # Calculate age
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            else:
                dt = ts
            result["age_seconds"] = time.time() - dt.timestamp()
            hours = result["age_seconds"] / 3600
            result["age_human"] = f"{int(hours)}h {int((hours % 1) * 60)}m"
    except Exception as e:
        result["error"] = str(e)
    return result

def check_log_file():
    """Check recent log entries."""
    result = {"exists": False, "tail": "", "lines": 0, "errors": []}
    if os.path.exists(LOG_FILE):
        result["exists"] = True
        try:
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
                result["lines"] = len(lines)
                # Get last 50 lines
                tail = lines[-50:] if len(lines) > 50 else lines
                result["tail"] = "".join(tail)
                # Look for errors
                for line in tail:
                    lower = line.lower()
                    if any(k in lower for k in ['error', 'exception', 'traceback', 'failed', 'crash']):
                        result["errors"].append(line.strip())
        except Exception as e:
            result["read_error"] = str(e)
    return result

def check_process_list():
    """Check if process is running via ps."""
    import subprocess
    result = {"running": False, "pid": None, "cmdline": ""}
    try:
        proc = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in proc.stdout.split('\n'):
            if SERVICE_NAME in line and 'python' in line.lower():
                result["running"] = True
                parts = line.split()
                if len(parts) > 1:
                    result["pid"] = parts[1]
                result["cmdline"] = line
                break
    except Exception as e:
        result["error"] = str(e)
    return result

def main():
    print(f"=== RUG_PULL_MONITOR STALE DIAGNOSTIC ===")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    print("[1] SUPERVISORD CONF CHECK")
    sup = check_supervisord_entry()
    print(f"  Config file exists: {sup['exists']}")
    print(f"  Enabled in config: {sup['enabled']}")
    if sup['exists']:
        status = "ENABLED" if sup['enabled'] else "DISABLED/MISSING"
        print(f"  Status: {status}")
    print()
    
    print("[2] PID FILE CHECK")
    pid = check_pid_file()
    print(f"  PID file exists: {pid['exists']}")
    print(f"  PID value: {pid['pid']}")
    print(f"  Process running: {pid['running']}")
    print(f"  Stale (no process): {pid['stale']}")
    if pid['age_seconds']:
        print(f"  File age: {pid['age_seconds']:.0f} seconds ({pid['age_seconds']/3600:.1f} hours)")
    if 'error' in pid:
        print(f"  Error: {pid['error']}")
    print()
    
    print("[3] SERVICE_HEALTH BEAT CHECK")
    health = check_service_health()
    print(f"  Found in table: {health['found']}")
    print(f"  Last heartbeat: {health['last_heartbeat']}")
    print(f"  Age: {health['age_human']}")
    print(f"  Stale (>2h): {health['age_seconds'] > 7200 if health['age_seconds'] else 'N/A'}")
    if 'error' in health:
        print(f"  Error: {health['error']}")
    print()
    
    print("[4] LOG FILE CHECK")
    logs = check_log_file()
    print(f"  Log file exists: {logs['exists']}")
    print(f"  Total lines: {logs['lines']}")
    print(f"  Recent errors ({len(logs['errors'])}):")
    for err in logs['errors'][:5]:
        print(f"    - {err[:120]}")
    if 'read_error' in logs:
        print(f"  Read error: {logs['read_error']}")
    print()
    
    print("[5] PROCESS TABLE CHECK")
    proc = check_process_list()
    print(f"  Process running: {proc['running']}")
    print(f"  PID: {proc['pid']}")
    if proc['cmdline']:
        print(f"  Cmdline: {proc['cmdline'][:200]}")
    print()
    
    print("=== DIAGNOSIS SUMMARY ===")
    issues = []
    if not sup['exists']:
        issues.append("MISSING: supervisord.conf entry not found")
    elif not sup['enabled']:
        issues.append("DISABLED: service is disabled in supervisor config")
    if not pid['exists']:
        issues.append("STALE: PID file missing (daemon never started or cleaned up)")
    elif pid['stale']:
        issues.append(f"STALE: PID file is {pid['age_seconds']/3600:.1f}h old, process dead")
    if not health['found']:
        issues.append("STALE: no heartbeat record in service_health")
    elif health['age_seconds'] and health['age_seconds'] > 7200:
        issues.append(f"STALE: last heartbeat {health['age_human']} ago (threshold: 2h)")
    if not proc['running']:
        issues.append("DEAD: no running process detected")
    
    if issues:
        print("Issues detected:")
        for issue in issues:
            print(f"  [!] {issue}")
    else:
        print("  No clear issues detected - may be transient or external kill.")
    
    print()
    print("RECOMMENDATION: Check if supervisord process is running, review log file above, and consider manual restart via supervisorctl.")

if __name__ == '__main__':
    main()