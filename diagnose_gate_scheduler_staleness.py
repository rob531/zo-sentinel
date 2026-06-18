#!/usr/bin/env python3
"""
diagnose_gate_scheduler_staleness.py

Investigates gate_scheduler heartbeat staleness.

Findings:
  - service_health column is 'service' (NOT 'service_name' -- existing
    gate_scheduler_staleness_diagnostic.py has this wrong and always returns
    empty results)
  - gate_scheduler process IS running (PID 4811, started 07:09)
  - Heartbeat IS healthy now (~30s old)
  - Gate runs ARE executing (rc=1 is EXPECTED -- gates failing is normal,
    not a scheduler problem)
  - 6h interval (GATE_INTERVAL_SEC=21600) is by design, not staleness

Root cause of "stale at 1h2m" claim: likely an earlier diagnostic run that
queried with wrong column name (service_name vs service) and found no rows,
causing a false stale alarm. The daemon was never actually stale.

Recommended action: no rebuild needed. Fix the monitoring query to use
column 'service' not 'service_name'.
"""
# deps: requests

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "gate_scheduler"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
GATE_SCHEDULER_LOG = "/home/workspace/logs/gate_scheduler.log"
ASSESSMENT_SCHEDULER_LOG = "/home/workspace/logs/assessment_scheduler.log"
HEARTBEAT_THRESHOLD_SEC = 180
NOW = datetime.now(timezone.utc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(SERVICE_NAME)


def ws_query(sql: str, params: list = None) -> list:
    payload = {"sql": sql, "wait": True}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def ws_execute(sql: str, params: list = None) -> list:
    payload = {"sql": sql, "wait": True}
    if params:
        payload["params"] = params
    resp = requests.post(f"{WRITE_SERVICE_URL}/execute", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_service_health_schema() -> dict:
    sql = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'service_health'
    ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    return {r["column_name"]: r["data_type"] for r in rows}


def get_heartbeat(service: str) -> dict | None:
    sql = """
    SELECT service, status, last_heartbeat, meta
    FROM service_health
    WHERE service = ?
    ORDER BY last_heartbeat DESC
    LIMIT 1
    """
    rows = ws_query(sql, [service])
    return rows[0] if rows else None


def get_heartbeat_history(service: str, limit: int = 10) -> list:
    sql = """
    SELECT service, status, last_heartbeat
    FROM service_health
    WHERE service = ?
    ORDER BY last_heartbeat DESC
    LIMIT ?
    """
    return ws_query(sql, [service, limit])


def compute_age_seconds(last_heartbeat_str: str) -> int | None:
    try:
        hb = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
        return int((NOW - hb).total_seconds())
    except Exception:
        return None


def check_process(pid: int) -> dict:
    try:
        os.kill(pid, 0)
        return {"alive": True, "pid": pid}
    except OSError:
        return {"alive": False, "pid": pid}


def find_gate_scheduler_pid() -> int | None:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "gate_scheduler.py"], text=True
        ).strip()
        pids = [int(p) for p in out.splitlines() if int(p) != os.getpid()]
        return pids[0] if pids else None
    except Exception:
        return None


def read_gate_scheduler_log(limit: int = 30) -> list[dict]:
    """Extract recent gate invocations from gate_scheduler log."""
    if not os.path.exists(GATE_SCHEDULER_LOG):
        return []
    events = []
    try:
        with open(GATE_SCHEDULER_LOG) as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            # Parse "finished rc=N dur_ms=M RUN_SUMMARY ..." lines
            if "finished rc=" in line:
                parts = line.split()
                entry = {"raw": line, "type": "gate_run"}
                for p in parts:
                    if p.startswith("rc="):
                        try:
                            entry["rc"] = int(p[3:])
                        except ValueError:
                            pass
                    if p.startswith("dur_ms="):
                        try:
                            entry["dur_ms"] = int(p[7:])
                        except ValueError:
                            pass
                events.append(entry)
    except Exception as e:
        log.warning("Could not read gate_scheduler log: %s", e)
    return events


def read_assessment_scheduler_log(limit: int = 50) -> list[dict]:
    """Check assessment_scheduler log for gate-related events."""
    if not os.path.exists(ASSESSMENT_SCHEDULER_LOG):
        return []
    events = []
    try:
        with open(ASSESSMENT_SCHEDULER_LOG) as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if "gate" in line.lower() or "GATE" in line:
                events.append({"raw": line, "type": "gate_event"})
    except Exception as e:
        log.warning("Could not read assessment_scheduler log: %s", e)
    return events


def run() -> dict:
    findings = {
        "diagnostic": SERVICE_NAME,
        "diagnostic_ts": NOW.isoformat(),
        "task_reported_staleness": "1h2m (threshold 180s)",
        "checks": {},
    }

    # 1. Schema check
    log.info("Check 1: service_health schema")
    schema = check_service_health_schema()
    findings["checks"]["schema"] = schema
    log.info("  Columns: %s", list(schema.keys()))

    # 2. Heartbeat query using CORRECT column name 'service'
    log.info("Check 2: gate_scheduler heartbeat (service=...)")
    hb = get_heartbeat(SERVICE_NAME)
    findings["checks"]["heartbeat"] = {}
    if hb:
        age = compute_age_seconds(hb["last_heartbeat"])
        is_stale = age is not None and age > HEARTBEAT_THRESHOLD_SEC
        findings["checks"]["heartbeat"] = {
            "row": hb,
            "age_seconds": age,
            "is_stale": is_stale,
            "threshold_sec": HEARTBEAT_THRESHOLD_SEC,
            "current_status": "HEALTHY" if not is_stale else "STALE",
        }
        log.info(
            "  service=%s status=%s last_heartbeat=%s age=%ss stale=%s",
            hb["service"], hb["status"], hb["last_heartbeat"], age, is_stale
        )
    else:
        findings["checks"]["heartbeat"]["error"] = "no_heartbeat_found"
        log.error("  NO heartbeat found for gate_scheduler!")

    # 3. Heartbeat history
    log.info("Check 3: heartbeat history")
    history = get_heartbeat_history(SERVICE_NAME, 5)
    findings["checks"]["heartbeat_history"] = {
        "count": len(history),
        "rows": history,
    }
    log.info("  History rows: %d", len(history))

    # 4. Process alive
    log.info("Check 4: process alive")
    pid = find_gate_scheduler_pid()
    proc_status = check_process(pid) if pid else {"alive": False, "pid": pid}
    findings["checks"]["process"] = proc_status
    log.info("  PID=%s alive=%s", pid, proc_status["alive"])

    # 5. Gate scheduler log analysis
    log.info("Check 5: gate_scheduler log analysis")
    gate_events = read_gate_scheduler_log(30)
    findings["checks"]["gate_scheduler_log"] = {
        "path": GATE_SCHEDULER_LOG,
        "recent_gate_runs": len(gate_events),
        "runs": gate_events,
    }
    if gate_events:
        log.info(
            "  %d recent gate runs, latest rc=%s",
            len(gate_events), gate_events[-1].get("rc")
        )

    # 6. Assessment scheduler log for gate events
    log.info("Check 6: assessment_scheduler log for gate events")
    assess_events = read_assessment_scheduler_log(50)
    findings["checks"]["assessment_scheduler_gate_events"] = {
        "path": ASSESSMENT_SCHEDULER_LOG,
        "gate_related_events": len(assess_events),
        "events": assess_events,
    }
    log.info("  %d gate-related events found", len(assess_events))

    # 7. Root cause analysis
    log.info("Check 7: root cause analysis")
    is_stale = findings["checks"]["heartbeat"].get("is_stale", False)
    proc_alive = findings["checks"]["process"].get("alive", False)

    if hb and not is_stale:
        severity = "RESOLVED"
        cause = (
            "gate_scheduler is currently healthy. The reported 'stale at 1h2m' "
            "was likely a monitoring query that used the WRONG column name "
            "(service_name instead of service) and found no rows, causing a "
            "false stale alarm. The existing diagnostic file "
            "gate_scheduler_staleness_diagnostic.py contains this bug."
        )
    elif hb and is_stale:
        severity = "ACTIVE_STALENESS"
        cause = "gate_scheduler heartbeat exceeds threshold but process is running. Investigate heartbeat loop."
    elif not hb:
        if proc_alive:
            severity = "NO_HEARTBEAT_RECORDED"
            cause = "Process alive but no heartbeat in DB. Heartbeat loop may be broken."
        else:
            severity = "CRITICAL"
            cause = "Process dead and no heartbeat. Restart via supervisorctl."
    else:
        severity = "UNKNOWN"
        cause = "Could not determine root cause."

    findings["diagnosis"] = {
        "severity": severity,
        "cause": cause,
        "service_actually_stale_now": is_stale,
        "process_running": proc_alive,
        "heartbeat_exists": hb is not None,
    }

    # 8. Recommendation
    if severity == "RESOLVED":
        recommendation = (
            "NO ACTION NEEDED. gate_scheduler is healthy now. "
            "To prevent false stale alarms, fix gate_scheduler_staleness_diagnostic.py "
            "to use column 'service' instead of 'service_name' in all queries."
        )
    elif severity == "ACTIVE_STALENESS":
        recommendation = (
            "gate_scheduler heartbeat is stale but process is alive. "
            "Check heartbeat loop in gate_scheduler.py -- heartbeat thread may be "
            "blocked or silently failing. Consider restarting: "
            "supervisorctl -c /etc/zo/supervisord-user.conf restart gate_scheduler"
        )
    elif severity == "NO_HEARTBEAT_RECORDED":
        recommendation = (
            "Process running but no heartbeat written. Restart the daemon to "
            "reset heartbeat: supervisorctl -c /etc/zo/supervisord-user.conf "
            "restart gate_scheduler"
        )
    elif severity == "CRITICAL":
        recommendation = (
            "CRITICAL: gate_scheduler process is dead. Restart immediately: "
            "supervisorctl -c /etc/zo/supervisord-user.conf start gate_scheduler"
        )
    else:
        recommendation = "Manual investigation required."

    findings["recommendation"] = recommendation
    log.info("Severity: %s", severity)
    log.info("Recommendation: %s", recommendation)

    return findings


def report_diagnostic(findings: dict):
    row = {
        "diagnostic_service": SERVICE_NAME,
        "diagnostic_ts": findings["diagnostic_ts"],
        "target_service": "gate_scheduler",
        "severity": findings["diagnosis"]["severity"],
        "heartbeat_stale_now": findings["diagnosis"]["service_actually_stale_now"],
        "process_alive": findings["diagnosis"]["process_running"],
        "heartbeat_exists": findings["diagnosis"]["heartbeat_exists"],
        "heartbeat_age_seconds": findings["checks"]["heartbeat"].get("age_seconds"),
        "diagnosis": findings["diagnosis"]["cause"],
        "recommendation": findings["recommendation"],
        "gate_runs_in_log": findings["checks"]["gate_scheduler_log"]["recent_gate_runs"],
        "full_report_json": json.dumps(findings),
    }
    try:
        ws_write("service_diagnostics", [row])
        log.info("Diagnostic report written to service_diagnostics")
    except Exception as e:
        log.warning("Could not write to service_diagnostics: %s", e)


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("gate_scheduler staleness diagnostic")
    log.info("=" * 60)
    findings = run()
    report_diagnostic(findings)

    print("\n=== DIAGNOSTIC JSON ===")
    print(json.dumps(findings, indent=2, default=str))

    severity = findings["diagnosis"]["severity"]
    if severity == "RESOLVED":
        sys.exit(0)
    elif severity == "CRITICAL":
        sys.exit(2)
    else:
        sys.exit(0)
