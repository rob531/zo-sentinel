#!/usr/bin/env python3
"""
Re-diagnostic utility for gate_scheduler staleness.
Previous diagnose_gate_scheduler_staleness.py was built but daemon remains stale at 4m (threshold 180s).
Checks daemon process health, supervisord status, logs, and compares against healthy gate_orchestrator.

Usage: python3 diagnose_gate_scheduler_staleness_v2.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "gate_scheduler"
COMPARISON_SERVICE = "gate_orchestrator"
STALENESS_THRESHOLD = 180  # seconds


def run_cmd(cmd, shell=True):
    """Run shell command and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1
    except Exception as e:
        return "", str(e), 1


def get_service_health(service: str) -> dict:
    """Query service_health table for a service's last heartbeat."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "sql": "SELECT service, last_heartbeat, status, meta FROM service_health WHERE service = ? ORDER BY last_heartbeat DESC LIMIT 1",
                "params": [service]
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows"):
                return data["rows"][0]
    except Exception as e:
        print(f"  [WARN] Could not query service_health for {service}: {e}")
    return {}


def calculate_staleness(last_heartbeat: str) -> float:
    """Calculate staleness in seconds from ISO timestamp."""
    if not last_heartbeat:
        return float('inf')
    try:
        last_ts = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        return (now - last_ts).total_seconds()
    except Exception:
        return float('inf')


def check_process_health(service_name: str) -> dict:
    """Check if a service process is running via /proc."""
    result = {
        "service": service_name,
        "pid": None,
        "state": None,
        "vmrss_kb": None,
        "threads": None,
        "uptime_sec": None,
        "cmdline": None,
    }
    
    # Try pgrep first
    pgrep_out, _, _ = run_cmd(f"pgrep -f '{service_name}'")
    if pgrep_out:
        try:
            result["pid"] = int(pgrep_out.split()[0])
        except (ValueError, IndexError):
            pass
    
    if not result["pid"]:
        return result
    
    pid = result["pid"]
    
    # Check /proc/{pid}/status
    status_file = f"/proc/{pid}/status"
    if os.path.exists(status_file):
        try:
            with open(status_file) as f:
                for line in f:
                    if line.startswith("State:"):
                        result["state"] = line.split(":", 1)[1].strip()
                    elif line.startswith("VmRSS:"):
                        result["vmrss_kb"] = int(line.split()[1])
                    elif line.startswith("Threads:"):
                        result["threads"] = int(line.split()[1])
        except Exception:
            pass
    
    # Check process start time for uptime
    stat_path = f"/proc/{pid}/stat"
    if os.path.exists(stat_path):
        try:
            with open(stat_path) as f:
                stat = f.read().split()
                start_time_jiffies = int(stat[19])
                # Get system uptime
                uptime_out, _, _ = run_cmd("cat /proc/uptime")
                if uptime_out:
                    system_uptime = float(uptime_out.split()[0])
                    # jiffies per second (usually 100)
                    hz = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
                    result["uptime_sec"] = system_uptime - (start_time_jiffies / hz)
        except Exception:
            pass
    
    # Check cmdline
    cmdline_file = f"/proc/{pid}/cmdline"
    if os.path.exists(cmdline_file):
        try:
            with open(cmdline_file) as f:
                result["cmdline"] = f.read().replace('\x00', ' ').strip()
        except Exception:
            pass
    
    return result


def check_supervisord_status(service_name: str) -> dict:
    """Check supervisord status for a service."""
    result = {"available": False, "status": None, "pid": None, "error": None}
    
    stdout, stderr, rc = run_cmd("supervisorctl status")
    if rc == 0 and stdout:
        result["available"] = True
        for line in stdout.splitlines():
            if service_name in line.lower():
                result["status"] = line.strip()
                # Extract PID if present
                for part in line.split():
                    if part.isdigit():
                        result["pid"] = int(part)
                        break
                break
    elif stderr and "not available" in stderr.lower():
        result["error"] = "supervisord not available"
    else:
        result["error"] = stderr[:200] if stderr else "unknown error"
    
    return result


def get_service_logs(service_name: str, lines: int = 50) -> str:
    """Get recent log entries for a service."""
    # Try supervisorctl tail first
    stdout, _, rc = run_cmd(f"supervisorctl tail -1000 {service_name}")
    if rc == 0 and stdout:
        all_lines = stdout.strip().split('\n')
        return '\n'.join(all_lines[-lines:])
    
    # Try common log locations
    log_paths = [
        f"/var/log/supervisor/{service_name}.log",
        f"/var/log/{service_name}.log",
        f"/home/workspace/logs/{service_name}.log",
        f"/home/workspace/zo_sentinel/logs/{service_name}.log",
    ]
    
    for log_path in log_paths:
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    content = f.read()
                all_lines = content.strip().split('\n')
                return '\n'.join(all_lines[-lines:])
            except Exception:
                pass
    
    return "[No log content found]"


def analyze_logs_for_errors(log_content: str) -> list:
    """Extract error lines from log content."""
    error_patterns = ['error', 'exception', 'failed', 'fatal', 'traceback', 'crash', 'timeout']
    error_lines = []
    for line in log_content.splitlines():
        lower = line.lower()
        if any(p in lower for p in error_patterns):
            error_lines.append(line.strip())
    return error_lines[-10:]  # Last 10 errors


def compare_health_status():
    """Compare health of gate_scheduler vs gate_orchestrator."""
    print("\n" + "=" * 60)
    print("SERVICE COMPARISON")
    print("=" * 60)
    
    services = [SERVICE_NAME, COMPARISON_SERVICE]
    comparison_data = {}
    
    for svc in services:
        print(f"\n--- {svc} ---")
        
        # Get service_health data
        health = get_service_health(svc)
        staleness = None
        if health.get("last_heartbeat"):
            staleness = calculate_staleness(health["last_heartbeat"])
            print(f"  last_heartbeat: {health['last_heartbeat']}")
            print(f"  staleness: {staleness:.1f}s ({staleness/60:.1f} min)")
            print(f"  status: {health.get('status', 'N/A')}")
        else:
            print(f"  last_heartbeat: NOT FOUND in service_health")
        
        # Get process health
        proc = check_process_health(svc)
        if proc["pid"]:
            print(f"  PID: {proc['pid']}")
            print(f"  State: {proc['state']}")
            print(f"  Threads: {proc['threads']}")
            print(f"  VmRSS: {proc['vmrss_kb']} KB" if proc['vmrss_kb'] else "")
            if proc['uptime_sec']:
                print(f"  Process uptime: {proc['uptime_sec']:.1f}s ({proc['uptime_sec']/60:.1f} min)")
        else:
            print(f"  PID: NOT RUNNING")
        
        # Get supervisord status
        super_status = check_supervisord_status(svc)
        if super_status['available']:
            print(f"  Supervisord: {super_status['status']}")
        elif super_status['error']:
            print(f"  Supervisord: {super_status['error']}")
        
        comparison_data[svc] = {
            "health": health,
            "staleness_sec": staleness,
            "process": proc,
            "supervisord": super_status,
        }
    
    # Diagnostic analysis
    print("\n" + "=" * 60)
    print("DIAGNOSTIC ANALYSIS")
    print("=" * 60)
    
    sched_data = comparison_data.get(SERVICE_NAME, {})
    orch_data = comparison_data.get(COMPARISON_SERVICE, {})
    
    sched_stale = sched_data.get("staleness_sec", float('inf'))
    orch_stale = orch_data.get("staleness_sec", 0)
    
    print(f"\nStaleness Summary:")
    print(f"  {SERVICE_NAME}: {sched_stale:.1f}s ({sched_stale/60:.1f} min) [threshold: {STALENESS_THRESHOLD}s]")
    print(f"  {COMPARISON_SERVICE}: {orch_stale:.1f}s ({orch_stale/60:.1f} min)")
    
    # Determine if stale
    is_stale = sched_stale > STALENESS_THRESHOLD
    print(f"\n  {SERVICE_NAME} is STALE: {is_stale}")
    
    if is_stale:
        print(f"\n[ISSUE DETECTED] {SERVICE_NAME} is stale ({sched_stale:.1f}s > {STALENESS_THRESHOLD}s threshold)")
        
        # Check if process is even running
        if not sched_data.get("process", {}).get("pid"):
            print("  ROOT CAUSE: Process not running at all")
        elif sched_data.get("process", {}).get("state") == "Z":
            print("  ROOT CAUSE: Process is a zombie (Z state)")
        else:
            # Check for other process issues
            proc = sched_data.get("process", {})
            if proc.get("threads", 1) == 1 and proc.get("state", "").startswith("S"):
                print("  POSSIBLE ROOT CAUSE: Process may be stuck sleeping (single thread, S state)")
            if proc.get("vmrss_kb", 0) > 500000:
                print("  POSSIBLE ROOT CAUSE: High memory usage (possible memory issue)")
    
    # Comparison insight
    if orch_stale < STALENESS_THRESHOLD and sched_stale > STALENESS_THRESHOLD:
        print(f"\n[INSIGHT] {COMPARISON_SERVICE} is healthy but {SERVICE_NAME} is not.")
        print("  This suggests a process-specific issue, not a system-wide problem.")
    
    return comparison_data


def check_logs():
    """Check logs for both services."""
    print("\n" + "=" * 60)
    print("LOG ANALYSIS")
    print("=" * 60)
    
    for svc in [SERVICE_NAME, COMPARISON_SERVICE]:
        print(f"\n--- {svc} logs ---")
        log_content = get_service_logs(svc, lines=30)
        print(f"[Last 30 lines]:")
        print(log_content[:2000])  # Truncate if too long
        
        errors = analyze_logs_for_errors(log_content)
        if errors:
            print(f"\n[Found {len(errors)} error lines]:")
            for e in errors:
                print(f"  {e[:150]}")
        else:
            print("[No error patterns found in logs]")


def check_cron_and_scheduled_jobs():
    """Check for conflicting cron jobs or other schedulers."""
    print("\n" + "=" * 60)
    print("SCHEDULED JOBS CHECK")
    print("=" * 60)
    
    # Check crontab
    stdout, _, rc = run_cmd("crontab -l 2>/dev/null")
    if rc == 0 and stdout:
        print("\n[User crontab]:")
        print(stdout)
    else:
        print("[No user crontab found]")
    
    # Check system crontab
    stdout, _, rc = run_cmd("cat /etc/crontab 2>/dev/null")
    if rc == 0 and stdout:
        print("\n[System crontab]:")
        print(stdout[:500])
    
    # Check for other scheduling mechanisms
    stdout, _, _ = run_cmd("ps aux | grep -E 'cron|cronie|anacron' | grep -v grep")
    if stdout:
        print("\n[Other scheduler processes]:")
        print(stdout[:500])


def print_summary():
    """Print diagnostic summary and recommendations."""
    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY & RECOMMENDATIONS")
    print("=" * 60)
    
    print("""
KEY FINDINGS TO LOOK FOR:
1. Is gate_scheduler process actually running?
2. What is the actual staleness value vs threshold (180s)?
3. Are there any Python exceptions or errors in logs?
4. Is there a deadlock or infinite loop preventing updates?
5. Is the process consuming excessive CPU or memory?
6. Are there zombie threads?

POSSIBLE CAUSES FOR STALENESS:
- Process stuck in infinite loop or deadlock
- Database connection issues (write_service unreachable)
- File lock contention
- Memory exhaustion causing slow processing
- Configuration mismatch between service and threshold
- Supervisord misconfiguration
- Heartbeat thread not running

RECOMMENDED ACTIONS:
1. Check 'supervisorctl tail gate_scheduler' in real-time
2. Look for Python tracebacks in stderr
3. Check if write_service on 127.0.0.1:8772 is responsive:
   curl -X POST http://127.0.0.1:8772/health
4. Try restarting: supervisorctl restart gate_scheduler
5. Compare process state between healthy and stale instances:
   cat /proc/<pid>/stack
6. Check for blocking I/O or network issues
""")


def main():
    print("=" * 60)
    print("GATE_SCHEDULER STALENESS DIAGNOSTIC TOOL v2")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Staleness threshold: {STALENESS_THRESHOLD}s")
    
    # Compare health status
    compare_health_status()
    
    # Check logs
    check_logs()
    
    # Check scheduled jobs
    check_cron_and_scheduled_jobs()
    
    # Print summary
    print_summary()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())