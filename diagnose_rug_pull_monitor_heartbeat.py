#!/usr/bin/env python3
"""
diagnose_rug_pull_monitor_heartbeat.py

Diagnostic utility for rug_pull_monitor daemon that has never heartbeat (age=never).
Per PRODUCT_SPEC §6: check service_health table, verify process via psutil,
verify supervisord configuration. Output findings to stdout for operator review.

PROTECTED file - do NOT rebuild rug_pull_monitor.py itself.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# deps: requests, psutil

QUERY_URL = "http://127.0.0.1:8772/query"

# Known locations for supervisord configs
SUPERVISORD_CONFIGS = [
    "/etc/zo/supervisord-user.conf",
    "/etc/zo/supervisor.conf",
    "/home/workspace/zo_sentinel/supervisord-user.conf",
    "/home/workspace/zo_sentinel/supervisord_sentinel_full.conf",
]

RUG_PULL_PROCESS_NAMES = [
    "rug_pull_monitor",
    "rug_pull_monitor_daemon",
]


def ws_query(sql: str, params: list = None) -> Dict[str, Any]:
    """Execute SELECT against DuckDB via write_service /query."""
    import requests

    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_service_health() -> Dict[str, Any]:
    """Check service_health table for rug_pull_monitor entries.

    Schema: service_health has columns: service, last_heartbeat, status, meta
    (NOT "timestamp" - that column does not exist).
    """
    sql = """
    SELECT service, last_heartbeat, status, meta
    FROM service_health
    WHERE service LIKE '%rug_pull%'
    ORDER BY last_heartbeat DESC
    LIMIT 10
    """
    try:
        result = ws_query(sql)
        rows = result.get("data", [])
        if not rows:
            # Fallback: try without filter to confirm table exists
            confirm = ws_query("SELECT COUNT(*) FROM service_health LIMIT 1")
            table_exists = confirm.get("data") is not None
            return {
                "found": False,
                "count": 0,
                "rows": [],
                "table_exists": table_exists,
                "error": None,
            }
        parsed = [
            {
                "service": r[0],
                "last_heartbeat": r[1],
                "status": r[2],
                "meta": r[3],
            }
            for r in rows
        ]
        return {
            "found": True,
            "count": len(parsed),
            "rows": parsed,
            "table_exists": True,
            "error": None,
        }
    except Exception as e:
        return {
            "found": False,
            "count": 0,
            "rows": [],
            "table_exists": None,
            "error": str(e),
        }


def check_process() -> Dict[str, Any]:
    """Check if rug_pull_monitor process is running via psutil."""
    try:
        import psutil

        found = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                name = proc.info.get("name") or ""
                combined = " ".join(cmdline) + " " + name
                if any(rp in combined.lower() for rp in ["rug_pull", "rug_pull_monitor"]):
                    found.append(
                        {
                            "pid": proc.info["pid"],
                            "name": name,
                            "cmdline": cmdline,
                            "create_time": datetime.fromtimestamp(
                                proc.info["create_time"], tz=timezone.utc
                            ).isoformat()
                            if proc.info.get("create_time")
                            else None,
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {"running": len(found) > 0, "processes": found, "error": None}
    except Exception as e:
        return {"running": False, "processes": [], "error": str(e)}


def check_supervisord_config() -> Dict[str, Any]:
    """Verify supervisord configuration for rug_pull_monitor."""
    rug_pull_programs = {}
    configs_found = []

    for config_path in SUPERVISORD_CONFIGS:
        if os.path.exists(config_path):
            configs_found.append(config_path)
            try:
                content = Path(config_path).read_text()
                in_program = False
                current_program = None
                current_lines = []

                for line in content.splitlines():
                    prog_match = line.strip().startswith("[program:")
                    if prog_match:
                        if current_program:
                            rug_pull_programs[current_program] = {
                                "config": config_path,
                                "lines": current_lines,
                            }
                        # Check if this program is rug_pull_monitor
                        program_name = line.strip().split("[program:")[1].split("]")[0]
                        if any(rp in program_name.lower() for rp in ["rug_pull"]):
                            current_program = program_name
                            current_lines = [line]
                        else:
                            current_program = None
                            current_lines = []
                    elif current_program is not None:
                        current_lines.append(line)

                if current_program:
                    rug_pull_programs[current_program] = {
                        "config": config_path,
                        "lines": current_lines,
                    }
            except Exception as e:
                rug_pull_programs[f"_error_{config_path}"] = {
                    "config": config_path,
                    "error": str(e),
                }

    # Also try supervisorctl status command
    supervisorctl_status = None
    try:
        result = subprocess.run(
            ["supervisorctl", "status", "rug_pull_monitor"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        supervisorctl_status = {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except FileNotFoundError:
        supervisorctl_status = {"error": "supervisorctl not found in PATH"}
    except subprocess.TimeoutExpired:
        supervisorctl_status = {"error": "supervisorctl timed out"}
    except OSError as e:
        supervisorctl_status = {"error": f"OSError: {e}"}
    except Exception as e:
        supervisorctl_status = {"error": str(e)}

    registered = len(rug_pull_programs) > 0
    return {
        "configs_scanned": SUPERVISORD_CONFIGS,
        "configs_found": configs_found,
        "programs_found": rug_pull_programs,
        "supervisorctl_status": supervisorctl_status,
        "registered_in_supervisord": registered,
    }


def check_daemon_file() -> Dict[str, Any]:
    """Check that the rug_pull_monitor daemon files exist and are executable."""
    candidates = [
        "/home/workspace/zo_sentinel/rug_pull_monitor.py",
        "/home/workspace/zo_sentinel/rug_pull_monitor_daemon.py",
    ]
    results = []
    for path in candidates:
        if os.path.exists(path):
            st = os.stat(path)
            results.append(
                {
                    "path": path,
                    "exists": True,
                    "size_bytes": st.st_size,
                    "mode": oct(st.st_mode),
                    "is_executable": os.access(path, os.X_OK),
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        else:
            results.append({"path": path, "exists": False})
    return {"files": results}


def diagnose() -> Dict[str, Any]:
    """Run all checks and return structured diagnostic findings."""
    ts = datetime.now(timezone.utc).isoformat()
    findings: Dict[str, Any] = {
        "timestamp": ts,
        "diagnostic": "rug_pull_monitor_heartbeat",
        "target": "rug_pull_monitor",
        "condition": "never_heartbeat",
        "checks": {},
        "verdict": None,
        "recovery_steps": [],
    }

    # 1. service_health
    try:
        sh = check_service_health()
        findings["checks"]["service_health"] = sh
    except Exception as e:
        findings["checks"]["service_health"] = {"error": str(e)}

    # 2. psutil process check
    try:
        proc = check_process()
        findings["checks"]["process"] = proc
    except Exception as e:
        findings["checks"]["process"] = {"error": str(e)}

    # 3. supervisord configuration
    try:
        svc = check_supervisord_config()
        findings["checks"]["supervisord"] = svc
    except Exception as e:
        findings["checks"]["supervisord"] = {"error": str(e)}

    # 4. daemon file
    try:
        df = check_daemon_file()
        findings["checks"]["daemon_file"] = df
    except Exception as e:
        findings["checks"]["daemon_file"] = {"error": str(e)}

    # --- Verdict ---
    sh = findings["checks"].get("service_health", {})
    proc = findings["checks"].get("process", {})
    svc = findings["checks"].get("supervisord", {})
    df = findings["checks"].get("daemon_file", {})

    never_heartbeat = not sh.get("found", False)
    process_running = proc.get("running", False)
    supervised = svc.get("registered_in_supervisord", False)
    daemon_exists = any(f.get("exists") for f in df.get("files", []))
    has_executable = any(
        f.get("exists") and f.get("is_executable")
        for f in df.get("files", [])
    )

    root_causes: list[str] = []
    severity = "INFO"

    if never_heartbeat and not process_running and not supervised:
        root_causes.append("DAEMON_NOT_RUNNING: rug_pull_monitor is not running and not supervised")
        severity = "CRITICAL"
    elif never_heartbeat and not supervised:
        root_causes.append("NOT_SUPERVISED: rug_pull_monitor has no supervisord entry")
        severity = "HIGH"
    elif never_heartbeat and process_running:
        root_causes.append("HEARTBEAT_STALL: daemon running but no service_health writes")
        severity = "HIGH"
    elif never_heartbeat and not daemon_exists:
        root_causes.append("DAEMON_MISSING: rug_pull_monitor.py not found on disk")
        severity = "CRITICAL"
    elif never_heartbeat and not has_executable:
        root_causes.append("NOT_EXECUTABLE: rug_pull_monitor.py lacks execute permission")
        severity = "HIGH"
    elif never_heartbeat:
        root_causes.append("UNKNOWN: daemon file exists and supervised but never heartbeated")
        severity = "MEDIUM"

    findings["verdict"] = {
        "never_heartbeat": never_heartbeat,
        "process_running": process_running,
        "supervised": supervised,
        "daemon_exists": daemon_exists,
        "has_executable": has_executable,
        "root_causes": root_causes,
        "severity": severity,
    }

    # Recovery steps
    steps: list[Dict[str, Any]] = []
    step_num = 1

    if not supervised and daemon_exists:
        steps.append(
            {
                "step": step_num,
                "action": "add_supervisord_entry",
                "description": "Add rug_pull_monitor to supervisord configuration",
                "details": {
                    "example_config": "[program:rug_pull_monitor]\n"
                    "command=python3 /home/workspace/zo_sentinel/rug_pull_monitor.py\n"
                    "directory=/home/workspace/zo_sentinel\n"
                    "autostart=true\n"
                    "autorestart=true\n"
                    "stdout_logfile=/home/workspace/logs/rug_pull_monitor.log\n"
                    "stderr_logfile=/home/workspace/logs/rug_pull_monitor.log\n",
                    "next_commands": [
                        "supervisorctl -c /etc/zo/supervisord-user.conf reread",
                        "supervisorctl -c /etc/zo/supervisord-user.conf update",
                        "supervisorctl -c /etc/zo/supervisord-user.conf start rug_pull_monitor",
                    ],
                },
            }
        )
        step_num += 1

    if not has_executable and daemon_exists:
        steps.append(
            {
                "step": step_num,
                "action": "chmod_executable",
                "description": "Make rug_pull_monitor.py executable",
                "command": "chmod +x /home/workspace/zo_sentinel/rug_pull_monitor.py",
            }
        )
        step_num += 1

    if supervised and not process_running:
        steps.append(
            {
                "step": step_num,
                "action": "start_daemon",
                "description": "Start the rug_pull_monitor daemon via supervisord",
                "command": "supervisorctl -c /etc/zo/supervisord-user.conf start rug_pull_monitor",
            }
        )
        step_num += 1

    if process_running and never_heartbeat:
        steps.append(
            {
                "step": step_num,
                "action": "investigate_heartbeat_failure",
                "description": "Daemon running but not writing to service_health - check logs",
                "log_path": "/home/workspace/logs/rug_pull_monitor.log",
                "checks": [
                    "Tail daemon stderr/stdout logs for errors",
                    "Verify write_service HTTP endpoint is reachable on 127.0.0.1:8772",
                    "Check for import errors at daemon startup",
                ],
            }
        )
        step_num += 1

    if steps:
        findings["recovery_steps"] = steps
    else:
        findings["recovery_steps"] = [
            {
                "step": 1,
                "action": "manual_investigation",
                "description": "All checks passed but daemon never heartbeated - review recovery steps above",
            }
        ]

    return findings


def main() -> int:
    """Run diagnostics and output findings to stdout."""
    findings = diagnose()
    print(json.dumps(findings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
