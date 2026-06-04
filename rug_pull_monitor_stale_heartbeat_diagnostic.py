#!/usr/bin/env python3
"""
rug_pull_monitor_stale_heartbeat_diagnostic.py

Diagnostic utility to determine root cause of rug_pull_monitor heartbeat
age of ~913h54m (stale > 28800s threshold).

Output: diagnostic summary identifying whether process is actually dead vs
reporting stale heartbeat, whether service file is misconfigured, and specific
next action for human operator.

PROTECTED FILE — diagnostic report only, no rebuild proposed.
"""

# deps: requests

import datetime
import json
import os
import subprocess
import sys
import time

WRITE_SERVICE = "http://127.0.0.1:8772"
STALE_THRESHOLD_SECONDS = 28800  # 8 hours
TARGET_SERVICE = "rug_pull_monitor"


def query_db(sql: str, params: list = None) -> dict:
    import requests

    resp = requests.post(
        f"{WRITE_SERVICE}/query",
        json={"sql": sql, "params": params or []},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_service_health(service: str) -> dict | None:
    rows = query_db(
        "SELECT service, status, last_heartbeat, meta FROM service_health WHERE service = %s",
        [service],
    ).get("rows", [])
    return rows[0] if rows else None


def get_supervisord_status() -> dict:
    """Try to query supervisord via XML-RPC; fall back to shell."""
    try:
        from xmlrpc.client import ServerProxy

        proxy = ServerProxy("http://127.0.0.1:9001/RPC2")
        all_procs = proxy.supervisor.getAllProcessInfo()
        rug_procs = [p for p in all_procs if TARGET_SERVICE in p.get("name", "")]
        return {" rug_procs": rug_procs, "source": "supervisor_xmlrpc"}
    except Exception as e:
        return {"error": str(e), "source": "xmlrpc_failed"}


def get_process_list() -> list[dict]:
    """Return running processes matching target service."""
    result = subprocess.run(
        ["ps", "aux"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    matches = []
    for line in result.stdout.splitlines():
        if TARGET_SERVICE in line.lower():
            parts = line.split()
            if len(parts) >= 11:
                matches.append({
                    "pid": parts[1],
                    "cpu": parts[2],
                    "mem": parts[3],
                    "cmdline": " ".join(parts[10:]),
                })
    return matches


def check_service_file() -> dict:
    """Check for systemd unit file and supervisord config entries."""
    checks = {}
    paths = [
        f"/etc/systemd/system/{TARGET_SERVICE}.service",
        f"/etc/systemd/system/multi-user.target.wants/{TARGET_SERVICE}.service",
        "/etc/zo/supervisord-user.conf",
        "/etc/zo/supervisor.conf",
    ]
    found = []
    for path in paths:
        try:
            with open(path) as f:
                content = f.read()
                if TARGET_SERVICE in content:
                    found.append({"path": path, "present": True})
        except FileNotFoundError:
            pass
        except PermissionError:
            found.append({"path": path, "present": "permission_denied"})
    checks["supervisor_configs_checked"] = paths
    checks["service_in_supervisor"] = found if found else []
    checks["systemd_unit_found"] = any(
        TARGET_SERVICE in str(f) for f in found
    )
    return checks


def compute_heartbeat_age(heartbeat_iso: str) -> float:
    """Return seconds since heartbeat."""
    hb = datetime.datetime.fromisoformat(heartbeat_iso.replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - hb.replace(tzinfo=datetime.timezone.utc)).total_seconds()


def format_age(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h{minutes}m"


def run() -> dict:
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report = {
        "diagnostic_id": f"rug_pull_stale_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "generated_at": now_iso,
        "target": TARGET_SERVICE,
        "stale_threshold_seconds": STALE_THRESHOLD_SECONDS,
    }

    # 1. DB heartbeat status
    health = get_service_health(TARGET_SERVICE)
    if health:
        age_sec = compute_heartbeat_age(health["last_heartbeat"])
        report["db_service_health"] = {
            "service": health["service"],
            "status_in_db": health["status"],
            "last_heartbeat": health["last_heartbeat"],
            "heartbeat_age_seconds": age_sec,
            "heartbeat_age_human": format_age(age_sec),
            "is_stale": age_sec > STALE_THRESHOLD_SECONDS,
        }
    else:
        report["db_service_health"] = {"found": False}
        return report

    # 2. Process running?
    procs = get_process_list()
    report["process_check"] = {
        "running": len(procs) > 0,
        "count": len(procs),
        "processes": procs,
    }

    # 3. Supervisord registration
    supervisor_info = get_supervisord_status()
    report["supervisord_check"] = supervisor_info

    # 4. Service file check
    service_file_info = check_service_file()
    report["service_file_check"] = service_file_info

    # 5. Diagnostic verdict
    is_dead = len(procs) == 0
    is_in_supervisor = (
        "rug_procs" in supervisor_info
        and len(supervisor_info.get("rug_procs", [])) > 0
    )
    is_registered = len(service_file_info.get("service_in_supervisor", [])) > 0
    is_stale = age_sec > STALE_THRESHOLD_SECONDS if health else True

    verdicts = []
    if is_dead:
        verdicts.append("PROCESS_DEAD")
    if is_stale:
        verdicts.append("HEARTBEAT_STALE")
    if not is_registered:
        verdicts.append("NOT_SUPERVISORD")
    if health and health.get("status") == "healthy" and is_dead:
        verdicts.append("SPURIOUS_HEALTHY_STATUS")

    report["verdict"] = verdicts

    # 6. Root cause determination
    if is_dead and not is_registered:
        root_cause = (
            "The rug_pull_monitor process is NOT running and has NO supervisord "
            "registration. It was apparently deployed manually or via a now-absent "
            "mechanism. The last heartbeat recorded in DB is from "
            f"{report['db_service_health']['heartbeat_age_human']} ago. "
            "The process likely terminated without being restarted."
        )
        next_action = (
            "MANUAL RESTART REQUIRED. No auto-restart will recover this daemon. "
            "Steps: (1) Locate the daemon script or entry point for rug_pull_monitor "
            "in the codebase (grep for 'rug_pull_monitor'), "
            "(2) Start it manually with the same working directory/environment, "
            "(3) Verify it registers a fresh heartbeat in service_health, "
            "(4) Add it to /etc/zo/supervisord-user.conf to prevent future loss."
        )
    elif is_dead and is_registered:
        root_cause = (
            "The rug_pull_monitor process is NOT running but IS registered in "
            "supervisord. Supervisord should have restarted it on failure. "
            "Likely cause: autorestart exhausted (maxreread/maxrestarts hit), "
            "or supervisord itself is not monitoring this process correctly."
        )
        next_action = (
            "CHECK supervisord: Run 'supervisorctl status' and "
            "'supervisorctl reread && supervisorctl update'. "
            "If process is in FATAL state, run 'supervisorctl restart rug_pull_monitor'. "
            "Check /etc/zo/supervisord-user.conf for autorestart/maxreread/maxrestarts "
            "settings. Also check process exit code in supervisord logs."
        )
    elif not is_dead:
        root_cause = (
            "The rug_pull_monitor process appears to be RUNNING (found in process list) "
            "but its heartbeat is stale. This suggests the heartbeat-writing loop "
            "is broken — the process is alive but not updating the DB."
        )
        next_action = (
            "The process is alive but unhealthy. "
            "Check logs for rug_pull_monitor to see heartbeat errors. "
            "Look for DB write failures, network issues, or heartbeat loop exceptions. "
            "Restart: supervisorctl restart rug_pull_monitor (or kill + restart manually)."
        )
    else:
        root_cause = "Insufficient data to determine root cause."
        next_action = "Manual investigation required."

    report["root_cause"] = root_cause
    report["next_action"] = next_action

    # 7. Additional context: all services heartbeat overview
    try:
        all_health = query_db(
            "SELECT service, status, last_heartbeat FROM service_health "
            "ORDER BY last_heartbeat DESC LIMIT 20"
        )
        report["other_services_summary"] = {
            "total_stale": sum(
                1 for r in all_health.get("rows", [])
                if compute_heartbeat_age(r["last_heartbeat"]) > STALE_THRESHOLD_SECONDS
            ),
            "count": len(all_health.get("rows", [])),
        }
    except Exception:
        report["other_services_summary"] = {"error": "could not fetch"}

    return report


def print_report(report: dict):
    print("=" * 70)
    print(f"RUG_PULL_MONITOR STALE HEARTBEAT DIAGNOSTIC")
    print(f"Generated: {report['generated_at']}")
    print(f"ID: {report['diagnostic_id']}")
    print("=" * 70)

    hb = report.get("db_service_health", {})
    if hb.get("found") is not False:
        print(f"\n[DB] service_health entry:")
        print(f"  Status in DB: {hb.get('status_in_db', 'N/A')}")
        print(f"  Last heartbeat: {hb.get('last_heartbeat', 'N/A')}")
        print(f"  Heartbeat age:  {hb.get('heartbeat_age_human', 'N/A')} "
              f"({hb.get('heartbeat_age_seconds', 0):.0f}s)")
        print(f"  Stale (>28800s): {hb.get('is_stale', 'N/A')}")

    pc = report.get("process_check", {})
    print(f"\n[PROCESS] Running: {pc.get('running', 'unknown')}")
    if pc.get("processes"):
        for p in pc["processes"]:
            print(f"  PID={p['pid']} CPU={p['cpu']}% MEM={p['mem']}% CMD={p['cmdline'][:80]}")
    else:
        print("  No process found matching rug_pull_monitor")

    sc = report.get("supervisord_check", {})
    print(f"\n[SUPERVISORD] Source: {sc.get('source', 'unknown')}")
    if "error" in sc:
        print(f"  XML-RPC failed: {sc['error']}")
    else:
        rps = sc.get("rug_procs", [])
        print(f"  rug_pull_monitor processes in supervisord: {len(rps)}")
        for p in rps:
            print(f"    name={p.get('name')} statename={p.get('statename')} "
                  f"pid={p.get('pid')} start={p.get('start')}")

    sf = report.get("service_file_check", {})
    print(f"\n[SERVICE FILES] Supervisor configs checked: {len(sf.get('supervisor_configs_checked', []))}")
    if sf.get("service_in_supervisor"):
        for entry in sf["service_in_supervisor"]:
            print(f"  FOUND in: {entry['path']}")
    else:
        print("  NOT found in any supervisord config")

    print(f"\n[VERDICT] {' | '.join(report.get('verdict', ['UNKNOWN']))}")

    print(f"\n[ROOT CAUSE]\n  {report.get('root_cause', 'Unknown')}")
    print(f"\n[NEXT ACTION]\n  {report.get('next_action', 'None')}")

    other = report.get("other_services_summary", {})
    if "error" not in other:
        print(f"\n[CONTEXT] Other services: {other.get('total_stale', '?')} stale "
              f"out of {other.get('count', '?')} recent services")
    print("=" * 70)


def main():
    try:
        report = run()
        print_report(report)

        # Write diagnostic report to logs dir
        log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "logs",
            "diagnose_rug_pull_monitor_stale.log",
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"DIAGNOSTIC RUN at {report['generated_at']}\n")
            json.dump(report, f, indent=2, default=str)
            f.write("\n")

        print(f"\nReport written to {log_path}")
        return 0
    except Exception as exc:
        print(f"ERROR: Diagnostic failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())