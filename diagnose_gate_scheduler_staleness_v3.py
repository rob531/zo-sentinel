#!/usr/bin/env python3
"""
diagnose_gate_scheduler_staleness_v3.py
Diagnostic script for gate_scheduler staleness issues (threshold: 180s)
Protected: Will NOT propose rebuild of gate_scheduler
"""

import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

# Threshold in seconds (from task: 180s, currently 4 minutes stale)
STALENESS_THRESHOLD_SECONDS = 180


def run_sql_query(query: str) -> Optional[str]:
    """Execute SQL query against service_health table."""
    try:
        result = subprocess.run(
            ["psql", "-h", "localhost", "-U", "sentinel", "-d", "zo_sentinel", 
             "-t", "-A", "-c", query],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"[WARN] SQL query failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"[ERROR] Database query failed: {e}")
        return None


def check_daemon_process() -> Dict[str, Any]:
    """Check if gate_scheduler daemon process is running."""
    result = {
        "running": False,
        "pid": None,
        "uptime_seconds": None,
        "memory_mb": None,
        "cpu_percent": None
    }
    
    try:
        ps_result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        for line in ps_result.stdout.split('\n'):
            if 'gate_scheduler' in line and 'grep' not in line:
                parts = line.split()
                if len(parts) >= 11:
                    result["running"] = True
                    result["pid"] = parts[1]
                    result["cpu_percent"] = float(parts[2])
                    result["memory_mb"] = float(parts[5]) / 1024
                    # Get uptime from process
                    try:
                        pid = parts[1]
                        uptime_result = subprocess.run(
                            ["ps", "-p", pid, "-o", "etimes=", "--no-headers"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if uptime_result.returncode == 0:
                            result["uptime_seconds"] = int(uptime_result.stdout.strip())
                    except:
                        pass
                break
                
    except Exception as e:
        print(f"[ERROR] Process check failed: {e}")
    
    return result


def get_service_health_status() -> Optional[Dict[str, Any]]:
    """Query service_health table for gate_scheduler status."""
    query = """
    SELECT service_name, status, last_heartbeat, last_successful_run, 
           error_count, last_error, created_at, updated_at
    FROM service_health 
    WHERE service_name = 'gate_scheduler'
    ORDER BY updated_at DESC 
    LIMIT 1;
    """
    
    result = run_sql_query(query)
    if not result:
        return None
    
    # Parse the result
    fields = result.split('|')
    if len(fields) >= 6:
        try:
            last_heartbeat = datetime.fromisoformat(fields[2])
            age_seconds = (datetime.now() - last_heartbeat).total_seconds()
            
            return {
                "service_name": fields[0],
                "status": fields[1],
                "last_heartbeat": fields[2],
                "last_successful_run": fields[3],
                "error_count": int(fields[4]) if fields[4] else 0,
                "last_error": fields[5] if len(fields) > 5 else None,
                "staleness_seconds": age_seconds,
                "is_stale": age_seconds > STALENESS_THRESHOLD_SECONDS
            }
        except Exception as e:
            print(f"[ERROR] Parse error: {e}")
            return None
    
    return None


def check_rug_pull_monitor() -> Dict[str, Any]:
    """Check rug_pull_monitor service health."""
    query = """
    SELECT service_name, status, last_heartbeat, error_count, last_error
    FROM service_health 
    WHERE service_name = 'rug_pull_monitor'
    ORDER BY updated_at DESC 
    LIMIT 1;
    """
    
    result = run_sql_query(query)
    status = {"running": False, "error": None}
    
    if result:
        fields = result.split('|')
        if len(fields) >= 5:
            status["running"] = fields[1] == "healthy"
            status["last_heartbeat"] = fields[2]
            status["error"] = fields[4] if len(fields) > 4 else None
    
    return status


def check_system_resources() -> Dict[str, Any]:
    """Check system resources that might affect daemon."""
    resources = {
        "disk_full": False,
        "memory_pressure": False,
        "load_average": None
    }
    
    try:
        # Check disk space
        df_result = subprocess.run(
            ["df", "-h", "/var/log/sentinel"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if df_result.returncode == 0:
            lines = df_result.stdout.strip().split('\n')
            if len(lines) > 1:
                usage = lines[1].split()[4].rstrip('%')
                if int(usage.rstrip('%')) > 90:
                    resources["disk_full"] = True
        
        # Check load average
        uptime_result = subprocess.run(
            ["uptime"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if uptime_result.returncode == 0:
            load = uptime_result.stdout.split('load average:')[1].strip().split(',')
            resources["load_average"] = float(load[0])
            
    except Exception as e:
        print(f"[WARN] Resource check failed: {e}")
    
    return resources


def identify_root_cause(
    health: Dict[str, Any],
    process: Dict[str, Any],
    resources: Dict[str, Any],
    rug_pull: Dict[str, Any]
) -> str:
    """Identify root cause of staleness."""
    
    causes = []
    
    # Check if daemon process is dead
    if not process["running"]:
        causes.append("DAEMON_DEAD: gate_scheduler process not running")
    
    # Check if daemon is running but unresponsive
    if process["running"] and health.get("is_stale"):
        causes.append("DAEMON_UNRESPONSIVE: Process running but not writing heartbeats")
        
        if process.get("cpu_percent", 0) < 1:
            causes.append("  -> Possible hang or blocking I/O")
        
        if process.get("memory_mb", 0) > 1000:
            causes.append("  -> High memory usage may indicate memory leak")
    
    # Check rug_pull_monitor dependency
    if not rug_pull["running"]:
        causes.append("DEPENDENCY_FAILURE: rug_pull_monitor is unhealthy")
        if rug_pull.get("error"):
            causes.append(f"  -> rug_pull_monitor error: {rug_pull['error'][:100]}")
    
    # System resource issues
    if resources["disk_full"]:
        causes.append("SYSTEM_ISSUE: Disk space critical")
    
    if resources.get("load_average", 0) > 10:
        causes.append("SYSTEM_ISSUE: High system load")
    
    # Database connectivity
    if health.get("error_count", 0) > 10:
        causes.append("DATABASE_ISSUE: High error count in service_health")
    
    return causes if causes else ["UNKNOWN: Insufficient data for diagnosis"]


def main():
    print("=" * 70)
    print("GATE_SCHEDULER STALENESS DIAGNOSTIC REPORT")
    print(f"Generated: {datetime.now().isoformat()}")
    print(f"Threshold: {STALENESS_THRESHOLD_SECONDS}s")
    print("=" * 70)
    print()
    
    # Gather diagnostic data
    print("[1/4] Querying service_health table...")
    health = get_service_health_status()
    
    print("[2/4] Checking daemon process status...")
    process = check_daemon_process()
    
    print("[3/4] Checking rug_pull_monitor dependency...")
    rug_pull = check_rug_pull_monitor()
    
    print("[4/4] Checking system resources...")
    resources = check_system_resources()
    
    print()
    print("-" * 70)
    print("DIAGNOSTIC FINDINGS")
    print("-" * 70)
    
    # Report service health
    print("\n[service_health table]")
    if health:
        staleness = health.get("staleness_seconds", 0)
        print(f"  Status:           {health.get('status', 'UNKNOWN')}")
        print(f"  Last Heartbeat:   {health.get('last_heartbeat', 'N/A')}")
        print(f"  Staleness:        {staleness:.1f}s ({staleness/60:.1f} minutes)")
        print(f"  Is Stale:         {health.get('is_stale', False)}")
        print(f"  Error Count:      {health.get('error_count', 0)}")
        if health.get('last_error'):
            print(f"  Last Error:       {health['last_error'][:200]}")
    else:
        print("  [ERROR] No service_health record found for gate_scheduler")
    
    # Report process status
    print("\n[Daemon Process]")
    print(f"  Running:          {process.get('running', False)}")
    if process.get("running"):
        print(f"  PID:              {process.get('pid', 'N/A')}")
        print(f"  CPU %:            {process.get('cpu_percent', 0):.1f}")
        print(f"  Memory (MB):      {process.get('memory_mb', 0):.1f}")
        print(f"  Uptime (s):       {process.get('uptime_seconds', 0)}")
    else:
        print("  [CRITICAL] gate_scheduler process is NOT running")
    
    # Report rug_pull_monitor
    print("\n[Dependency: rug_pull_monitor]")
    print(f"  Healthy:          {rug_pull.get('running', False)}")
    if rug_pull.get('last_heartbeat'):
        print(f"  Last Heartbeat:   {rug_pull['last_heartbeat']}")
    if rug_pull.get('error'):
        print(f"  Error:            {rug_pull['error'][:100]}")
    
    # Report system resources
    print("\n[System Resources]")
    print(f"  Disk Full:        {resources.get('disk_full', False)}")
    print(f"  Load Average:     {resources.get('load_average', 'N/A')}")
    
    # Root cause analysis
    print("\n" + "-" * 70)
    print("ROOT CAUSE ANALYSIS")
    print("-" * 70)
    
    causes = identify_root_cause(health, process, resources, rug_pull)
    for cause in causes:
        print(f"  • {cause}")
    
    # Recommended action
    print("\n" + "-" * 70)
    print("RECOMMENDED HUMAN ACTION")
    print("-" * 70)
    
    if not process.get("running"):
        print("""
  IMMEDIATE ACTION REQUIRED:
  
  1. Check systemd service status:
     $ sudo systemctl status gate_scheduler
  
  2. Check logs for crash/exit:
     $ sudo journalctl -u gate_scheduler -n 50 --no-pager
  
  3. If service file needs review (per Appendix A):
     $ sudo systemctl cat gate_scheduler
     $ sudo systemctl edit gate_scheduler
  
  4. Restart the service:
     $ sudo systemctl restart gate_scheduler
  
  5. Monitor recovery:
     $ watch -n 5 'psql -t -c "SELECT staleness_seconds FROM service_health 
          WHERE service_name = '\''gate_scheduler'\'' ORDER BY updated_at DESC LIMIT 1;"'
""")
    elif rug_pull and not rug_pull.get("running"):
        print("""
  ACTION REQUIRED: Dependency service unhealthy
  
  1. Restart rug_pull_monitor (per Appendix A):
     $ sudo systemctl restart rug_pull_monitor
  
  2. Check rug_pull_monitor logs:
     $ sudo journalctl -u rug_pull_monitor -n 30 --no-pager
  
  3. If service file needs review:
     $ sudo systemctl cat rug_pull_monitor
  
  4. After rug_pull_monitor recovers, verify gate_scheduler heartbeat resumes
""")
    else:
        print("""
  POSSIBLE ACTIONS:
  
  1. Graceful restart (recommended first step):
     $ sudo systemctl restart gate_scheduler
  
  2. Check for blocking operations in logs:
     $ sudo journalctl -u gate_scheduler -n 100 --since "10 minutes ago"
  
  3. Verify database connectivity:
     $ psql -h localhost -U sentinel -d zo_sentinel -c "SELECT 1"
  
  4. If issue persists, escalate to on-call SRE
""")
    
    # Summary
    print("-" * 70)
    print("SUMMARY")
    print("-" * 70)
    
    staleness_actual = health.get('staleness_seconds', 0) if health else 999
    staleness_minutes = staleness_actual / 60
    
    print(f"""
  gate_scheduler is {staleness_minutes:.1f} minutes stale (threshold: {STALENESS_THRESHOLD_SECONDS}s)
  
  Daemon Status:        {'RUNNING' if process.get('running') else 'DEAD'}
  Dependency Status:    {'HEALTHY' if rug_pull.get('running') else 'UNHEALTHY'}
  
  NOTE: Rebuild of gate_scheduler is PROTECTED and was NOT proposed.
  Recommended: Service restart or service file review per Appendix A.
""")
    
    print("=" * 70)
    print("END OF DIAGNOSTIC REPORT")
    print("=" * 70)
    
    return 1  # Exit code 1 indicates issue detected


if __name__ == "__main__":
    sys.exit(main())