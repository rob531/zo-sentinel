import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Optional


def diagnose_rug_pull_monitor_staleness() -> dict:
    """Diagnose why rug_pull_monitor heartbeat is stale."""
    results = {
        "diagnosis_time": datetime.utcnow().isoformat(),
        "service": "rug_pull_monitor",
        "stale_heartbeat_hours": 114.41666667,
        "findings": {},
        "issues": [],
        "recommendations": []
    }
    
    # 1. Read error logs from /var/log/sentinel/rug_pull_monitor*.log
    log_dir = "/var/log/sentinel"
    log_files = []
    if os.path.isdir(log_dir):
        for f in os.listdir(log_dir):
            if f.startswith("rug_pull_monitor") and f.endswith(".log"):
                log_files.append(os.path.join(log_dir, f))
    
    recent_errors = []
    for log_path in sorted(log_files, key=os.path.getmtime, reverse=True)[:3]:
        try:
            # Read last 500 lines for errors
            result = subprocess.run(
                ["tail", "-n", "500", log_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            lines = result.stdout.split("\n")
            for line in lines:
                if any(kw in line.lower() for kw in ["error", "exception", "failed", "traceback", "crash"]):
                    recent_errors.append(f"[{log_path}] {line}")
            # Check for gaps in log
            timestamps = re.findall(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', result.stdout)
            results["findings"]["log_gaps"] = len(timestamps) < 10 if timestamps else True
        except Exception as e:
            results["findings"][f"log_read_error_{log_path}"] = str(e)
    
    results["findings"]["recent_errors"] = recent_errors[-20:]
    
    # 2. Inspect supervisord service definition
    supervisord_conf_paths = [
        "/etc/supervisord.conf",
        "/etc/supervisor/supervisord.conf",
        "/home/workspace/zo_sentinel/supervisord.conf"
    ]
    service_config = None
    for conf_path in supervisord_conf_paths:
        if os.path.exists(conf_path):
            try:
                with open(conf_path, 'r') as f:
                    content = f.read()
                # Look for rug_pull_monitor program section
                match = re.search(r'\[program:rug_pull_monitor\](.*?)(?=\n\[|\Z)', content, re.DOTALL | re.IGNORECASE)
                if match:
                    service_config = match.group(1)
                    break
            except Exception:
                pass
    
    if service_config:
        results["findings"]["supervisord_config"] = service_config
        # Check for common issues
        if "pidfile" in service_config:
            pid_match = re.search(r'pidfile\s*=\s*(.+)', service_config)
            if pid_match:
                pidfile_path = pid_match.group(1).strip()
                if not os.path.exists(os.path.dirname(pidfile_path)):
                    results["issues"].append(f"pidfile directory does not exist: {pidfile_path}")
        if "command" in service_config:
            cmd_match = re.search(r'command\s*=\s*(.+)', service_config)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                if not os.path.exists(cmd.split()[0] if "python" in cmd else cmd.split()[0]):
                    results["issues"].append(f"command executable not found: {cmd}")
        if "autorestart" not in service_config or "autorestart=false" in service_config.lower():
            results["issues"].append("autorestart is disabled - service won't auto-recover from crashes")
    else:
        results["issues"].append("no supervisord program definition found for rug_pull_monitor")
    
    # 3. Check process table for zombie/stuck state
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=10
        )
        rug_pull_procs = []
        zombie_found = False
        for line in result.stdout.split("\n"):
            if "rug_pull_monitor" in line.lower() and "grep" not in line:
                parts = line.split()
                if len(parts) > 7:
                    state = parts[7] if parts[7] in ["R", "S", "D", "Z", "T", "I"] else "unknown"
                    if state == "Z":
                        zombie_found = True
                    rug_pull_procs.append({
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "state": state,
                        "etime": parts[-1] if len(parts) > 9 else "unknown"
                    })
        results["findings"]["running_processes"] = rug_pull_procs
        results["findings"]["zombie_found"] = zombie_found
        if zombie_found:
            results["issues"].append("rug_pull_monitor process is in zombie state")
        if len(rug_pull_procs) == 0:
            results["issues"].append("rug_pull_monitor process is not running")
        elif len(rug_pull_procs) > 1:
            results["issues"].append(f"multiple instances running ({len(rug_pull_procs)}) - possible pidfile corruption")
    except Exception as e:
        results["findings"]["ps_error"] = str(e)
    
    # 4. Query write_service for last_successful_write timestamp
    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/write",
            json={"table": "service_health", "rows": {}, "wait": True},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            # Query health records
            health_resp = requests.post(
                "http://127.0.0.1:8772/write",
                json={
                    "table": "service_health",
                    "rows": {},
                    "query": "SELECT service, last_heartbeat, last_successful_write FROM service_health WHERE service = 'rug_pull_monitor' ORDER BY last_heartbeat DESC LIMIT 1"
                },
                timeout=5
            )
            if health_resp.status_code == 200:
                results["findings"]["write_service_query_result"] = health_resp.json()
    except Exception as e:
        results["findings"]["write_service_error"] = str(e)
        results["issues"].append(f"cannot reach write_service: {e}")
    
    # Generate recommendations based on findings
    if "process is not running" in results["issues"]:
        results["recommendations"].append("Restart rug_pull_monitor via: supervisorctl start rug_pull_monitor")
    if zombie_found:
        results["recommendations"].append("Kill zombie: kill -9 <pid> and restart service")
    if "autorestart is disabled" in results["issues"]:
        results["recommendations"].append("Enable autorestart in supervisord.conf: autorestart=true")
    if "log_gaps" in results["findings"]:
        results["recommendations"].append("Check disk space and log rotation configuration")
    
    return results


if __name__ == "__main__":
    import json
    import logging
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    logger = logging.getLogger("diagnose_rug_pull_monitor")
    logger.info("Starting rug_pull_monitor staleness diagnosis")
    
    results = diagnose_rug_pull_monitor_staleness()
    print(json.dumps(results, indent=2, default=str))