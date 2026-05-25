#!/usr/bin/env python3
"""
diagnose_rug_pull_stale_v2.py
Diagnostic-only module for rug_pull_monitor (age=135h0m, status=stale).
DO NOT attempt to restart - only diagnoses and reports.
"""

import os
import time
import requests
import psutil
from datetime import datetime

SERVICE_NAME = "rug_pull_monitor"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

def get_last_heartbeat():
    """Query write_service for current heartbeat timestamp."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT last_heartbeat FROM service_health WHERE service = '{SERVICE_NAME}'"},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            return rows[0].get("last_heartbeat")
        return None
    except Exception as e:
        return f"ERROR: {str(e)}"

def check_process_alive():
    """Check if rug_pull_monitor process is alive via system APIs."""
    result = {
        "pid_file_found": os.path.exists(PID_FILE),
        "pid": None,
        "running": False,
        "status": None,
        "memory_mb": None,
        "cpu_percent": None
    }
    if result["pid_file_found"]:
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            result["pid"] = pid
            if psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                result["running"] = True
                result["status"] = proc.status()
                result["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 2)
                result["cpu_percent"] = proc.cpu_percent(interval=0.1)
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied) as e:
            result["status"] = f"ERROR: {str(e)}"
    return result

def get_service_health_record():
    """Get full service health record from write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT * FROM service_health WHERE service = '{SERVICE_NAME}'"},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            return rows[0]
        return {}
    except Exception as e:
        return {"error": str(e)}

def format_timestamp(ts):
    """Format timestamp for display."""
    if ts is None:
        return "NULL"
    if isinstance(ts, str) and ts.startswith("ERROR"):
        return ts
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = ts
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)

def calculate_age_seconds(ts):
    """Calculate age of heartbeat in seconds."""
    if ts is None or (isinstance(ts, str) and ts.startswith("ERROR")):
        return None
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = ts
        return (datetime.now() - dt).total_seconds()
    except Exception:
        return None

def run_diagnostics():
    """Run all diagnostic checks and return report."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "service": SERVICE_NAME,
        "service_type": "rug_pull_monitor",
        "known_status": "stale",
        "known_age": "135h0m",
        "diagnostics": {}
    }
    
    print("=" * 60)
    print(f"ZO-SENTINEL Diagnostic Report: {SERVICE_NAME}")
    print(f"Run at: {report['timestamp']}")
    print("=" * 60)
    print()
    
    last_hb = get_last_heartbeat()
    age_sec = calculate_age_seconds(last_hb)
    age_hr = round(age_sec / 3600, 2) if age_sec else None
    
    print("[1] DATABASE HEARTBEAT CHECK")
    print(f"    Last heartbeat: {format_timestamp(last_hb)}")
    if age_sec is not None:
        print(f"    Age: {age_sec:.0f} seconds ({age_hr} hours)")
        print(f"    Stale threshold (>1h): {'YES' if age_sec > 3600 else 'NO'}")
    else:
        print("    Age: UNKNOWN (no heartbeat record)")
    print()
    
    proc_info = check_process_alive()
    
    print("[2] PROCESS LIVELINESS CHECK")
    print(f"    PID file exists: {'YES' if proc_info['pid_file_found'] else 'NO'}")
    print(f"    PID from file: {proc_info['pid']}")
    print(f"    Process running: {'YES' if proc_info['running'] else 'NO'}")
    print(f"    Process status: {proc_info['status']}")
    if proc_info['memory_mb']:
        print(f"    Memory RSS: {proc_info['memory_mb']} MB")
    if proc_info['cpu_percent']:
        print(f"    CPU %: {proc_info['cpu_percent']}")
    print()
    
    health_record = get_service_health_record()
    
    print("[3] SERVICE HEALTH RECORD")
    if health_record:
        for k, v in health_record.items():
            if k != 'error':
                print(f"    {k}: {v}")
    else:
        print("    No record found in service_health table")
    if health_record.get('error'):
        print(f"    Query error: {health_record['error']}")
    print()
    
    report["diagnostics"]["heartbeat"] = {
        "last_heartbeat": str(last_hb),
        "age_seconds": age_sec,
        "age_hours": age_hr,
        "is_stale": age_sec > 3600 if age_sec else True
    }
    report["diagnostics"]["process"] = proc_info
    report["diagnostics"]["health_record"] = health_record
    
    print("[4] DIAGNOSTIC SUMMARY")
    findings = []
    
    if not last_hb:
        findings.append("CRITICAL: No heartbeat record in database")
    elif age_sec and age_sec > 3600:
        findings.append(f"WARNING: Heartbeat is {age_hr} hours old (stale)")
    else:
        findings.append("OK: Heartbeat is current")
    
    if not proc_info["running"]:
        findings.append("CRITICAL: No active process found")
    else:
        findings.append("OK: Process is alive")
        if proc_info["status"] != psutil.STATUS_RUNNING:
            findings.append(f"NOTE: Process status is '{proc_info['status']}' not RUNNING")
    
    if not health_record:
        findings.append("WARNING: No health record in service_health table")
    
    for i, finding in enumerate(findings, 1):
        print(f"    {i}. {finding}")
    
    report["findings"] = findings
    
    print()
    print("[5] CONCLUSION")
    critical_count = sum(1 for f in findings if "CRITICAL" in f)
    warning_count = sum(1 for f in findings if "WARNING" in f)
    
    if critical_count > 0:
        report["conclusion"] = "UNHEALTHY - Critical issues detected"
        report["action_recommended"] = "Manual intervention required"
        print(f"    Status: {report['conclusion']}")
        print(f"    Critical issues: {critical_count}")
        print(f"    Warnings: {warning_count}")
        print(f"    Action: {report['action_recommended']}")
    elif warning_count > 0:
        report["conclusion"] = "DEGRADED - Warnings detected"
        report["action_recommended"] = "Monitor closely"
        print(f"    Status: {report['conclusion']}")
        print(f"    Warnings: {warning_count}")
        print(f"    Action: {report['action_recommended']}")
    else:
        report["conclusion"] = "HEALTHY - No issues detected"
        report["action_recommended"] = "None"
        print(f"    Status: {report['conclusion']}")
        print(f"    Action: {report['action_recommended']}")
    
    print()
    print("=" * 60)
    print("NOTE: This is a diagnostic report only. No restart attempted.")
    print("=" * 60)
    
    return report

if __name__ == '__main__':
    run_diagnostics()