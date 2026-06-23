#!/usr/bin/env python3
"""
Diagnostic utility for stale internal services.

Checks three services at 4h37m staleness:
  1. write_service   - PROTECTED (diagnostic only, no restart)
  2. anti_entropy    - candidate for restart if process is dead
  3. wisdom_synthesiser - candidate for restart if process is dead

Checks: ps liveness, write_service heartbeat rows, log availability.
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("diagnose_stale_internal_services_v2")

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
HEALTH_URL = f"{WRITE_SERVICE_URL}/health"

# Target services and their log paths
SERVICES = {
    "write_service": {
        "protected": True,
        "log_path": "/var/log/zo_sentinel/write_service.log",
        "pid_file": "/tmp/write_service.pid",
    },
    "anti_entropy": {
        "protected": False,
        "log_path": "/var/log/zo_sentinel/anti_entropy.log",
        "pid_file": "/tmp/anti_entropy.pid",
    },
    "wisdom_synthesiser": {
        "protected": False,
        "log_path": "/var/log/zo_sentinel/wisdom_synthesiser.log",
        "pid_file": "/tmp/wisdom_synthesiser.pid",
    },
}

# Staleness threshold for this diagnostic run (4h37m = 277 min)
STALE_THRESHOLD_MINUTES = 4 * 60 + 37  # 277 minutes


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service with parameterized SQL."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error("QUERY ERROR: %s", e)
        return []


def ws_write(table: str, rows: dict[str, Any]) -> bool:
    """Write rows to a table via write_service."""
    try:
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("WRITE ERROR: %s", e)
        return False


def check_write_service_reachable() -> dict[str, Any]:
    """Check write_service /health endpoint."""
    result: dict[str, Any] = {"reachable": False, "response_time_ms": None, "error": None}
    try:
        start = time.time()
        resp = requests.get(HEALTH_URL, timeout=5)
        elapsed_ms = round((time.time() - start) * 1000, 2)
        result["reachable"] = True
        result["response_time_ms"] = elapsed_ms
        result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result


def get_heartbeat(service_name: str) -> Optional[str]:
    """Get last_heartbeat from service_health for a given service."""
    rows = ws_query(
        f"SELECT last_heartbeat FROM service_health WHERE service = '{service_name}'"
    )
    if rows:
        return rows[0].get("last_heartbeat")
    return None


def check_process_liveness(pid_file: str) -> dict[str, Any]:
    """Check if a process is alive via PID file and os.kill(pid, 0)."""
    info: dict[str, Any] = {
        "pid_file_exists": False,
        "pid": None,
        "is_running": False,
        "uptime_seconds": None,
    }
    pf = Path(pid_file)
    if pf.exists():
        info["pid_file_exists"] = True
        try:
            pid_str = pf.read_text().strip()
            if pid_str:
                info["pid"] = int(pid_str)
                try:
                    import os as _os

                    _os.kill(info["pid"], 0)  # signal 0 = check existence
                    info["is_running"] = True
                    # estimate uptime via /proc/<pid>/stat mtime
                    proc_path = Path(f"/proc/{info['pid']}")
                    if proc_path.exists():
                        stat_path = proc_path / "stat"
                        if stat_path.exists():
                            info["uptime_seconds"] = round(time.time() - stat_path.stat().st_mtime)
                except (ProcessLookupError, PermissionError):
                    info["is_running"] = False
        except (ValueError, OSError) as e:
            info["error"] = str(e)
    return info


def check_process_ps(pattern: str) -> bool:
    """Check if a process matching `pattern` is present in ps aux."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if pattern in line and "grep" not in line and "diagnose" not in line:
                return True
        return False
    except Exception as e:
        logger.error("ps aux error: %s", e)
        return False


def tail_log(log_path: str, lines: int = 30) -> list[str]:
    """Return the last N lines of a log file."""
    p = Path(log_path)
    if not p.exists():
        return []
    try:
        with open(p) as f:
            all_lines = f.readlines()
        return all_lines[-lines:] if len(all_lines) >= lines else all_lines
    except OSError as e:
        logger.error("Cannot read log %s: %s", log_path, e)
        return []


def parse_last_log_timestamp(log_lines: list[str]) -> Optional[datetime]:
    """Parse the most recent ISO-ish timestamp from log lines."""
    import re

    ts_re = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")
    for line in reversed(log_lines):
        m = ts_re.search(line)
        if m:
            try:
                raw = m.group(1).replace(" ", "T")
                return datetime.fromisoformat(raw)
            except ValueError:
                continue
    return None


def heartbeat_age_seconds(heartbeat_str: Optional[str]) -> Optional[float]:
    """Return seconds since heartbeat string, or None if unparseable."""
    if not heartbeat_str:
        return None
    try:
        # Handle both 'Z' suffix and '+00:00' suffix
        clean = heartbeat_str.replace("Z", "").replace("+00:00", "")
        hb_dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - hb_dt).total_seconds()
    except ValueError:
        return None


def diagnose_service(
    name: str, cfg: dict[str, Any]
) -> dict[str, Any]:
    """
    Run all checks for a single service and return a structured finding dict.
    """
    result: dict[str, Any] = {
        "service": name,
        "protected": cfg.get("protected", False),
        "heartbeat": None,
        "heartbeat_age_seconds": None,
        "is_stale": False,
        "process_liveness": {},
        "process_in_ps": False,
        "log_last_timestamp": None,
        "log_last_age_seconds": None,
        "recommendation": "none",
        "protected_note": "",
    }

    # 1. Heartbeat
    hb = get_heartbeat(name)
    result["heartbeat"] = hb
    age = heartbeat_age_seconds(hb)
    result["heartbeat_age_seconds"] = age
    if age is not None:
        result["is_stale"] = age > STALE_THRESHOLD_MINUTES * 60

    # 2. Process liveness via PID file
    result["process_liveness"] = check_process_liveness(cfg["pid_file"])

    # 3. Process in ps aux
    result["process_in_ps"] = check_process_ps(name)

    # 4. Log availability
    log_lines = tail_log(cfg["log_path"])
    if log_lines:
        ts = parse_last_log_timestamp(log_lines)
        result["log_last_timestamp"] = ts.isoformat() if ts else None
        if ts:
            now = datetime.now(timezone.utc)
            # naive comparison if log ts has no tz
            ts_aware = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
            result["log_last_age_seconds"] = (now - ts_aware).total_seconds()

    # 5. Recommendation
    is_dead = not result["process_in_ps"] and not result["process_liveness"].get("is_running", False)
    is_stale = result["is_stale"]

    if cfg.get("protected"):
        result["recommendation"] = "diagnose_only"
        result["protected_note"] = "write_service is protected - diagnostic only, no restart"
    elif is_dead:
        result["recommendation"] = "restart_candidate"
    elif is_stale:
        result["recommendation"] = "monitor"
    else:
        result["recommendation"] = "healthy"

    return result


def write_diagnostic_record(results: list[dict[str, Any]]) -> bool:
    """Write diagnostic summary to service_diagnostics table."""
    record_id = f"stale_internal_{int(time.time())}"
    rows = {
        record_id: {
            "service": "diagnose_stale_internal_services",
            "diagnostic_type": "staleness_trio",
            "services_checked": json.dumps([r["service"] for r in results]),
            "stale_services": json.dumps(
                [r["service"] for r in results if r["is_stale"]]
            ),
            "restart_candidates": json.dumps(
                [
                    r["service"]
                    for r in results
                    if r["recommendation"] == "restart_candidate"
                ]
            ),
            "details": json.dumps(
                {
                    r["service"]: {
                        "heartbeat_age_seconds": r["heartbeat_age_seconds"],
                        "is_stale": r["is_stale"],
                        "process_in_ps": r["process_in_ps"],
                        "log_last_age_seconds": r.get("log_last_age_seconds"),
                        "recommendation": r["recommendation"],
                    }
                    for r in results
                }
            ),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "target_server_id": "diagnose_stale_internal_services",
        }
    }
    return ws_write("service_diagnostics", rows)


def print_report(results: list[dict[str, Any]]) -> None:
    """Pretty-print the diagnostic report."""
    sep = "=" * 70
    print(f"\n{sep}")
    print("  STALE INTERNAL SERVICES DIAGNOSTIC REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"  Staleness threshold: {STALE_THRESHOLD_MINUTES} minutes (4h37m)")
    print(sep)

    for r in results:
        print(f"\n  {'-' * 68}")
        protected_tag = " [PROTECTED]" if r["protected"] else ""
        print(f"  SERVICE: {r['service']}{protected_tag}")

        # Heartbeat
        age_s = r.get("heartbeat_age_seconds")
        if age_s is not None:
            age_hm = f"{int(age_s//3600)}h{int((age_s%3600)//60):02d}m"
            stale_marker = " **STALE**" if r["is_stale"] else ""
            print(f"    Heartbeat age : {age_hm} ({age_s:.0f}s){stale_marker}")
        else:
            print(f"    Heartbeat age : NO RECORD")

        # Process
        pl = r.get("process_liveness", {})
        print(f"    PID file exists: {pl.get('pid_file_exists', False)}")
        print(f"    Process in ps   : {r.get('process_in_ps', False)}")
        print(f"    Process alive   : {pl.get('is_running', False)}")
        pid_val = pl.get("pid")
        if pid_val:
            print(f"    PID             : {pid_val}")
        up_s = pl.get("uptime_seconds")
        if up_s:
            print(f"    Process uptime  : {up_s:.0f}s")

        # Log
        log_age = r.get("log_last_age_seconds")
        if log_age is not None:
            print(f"    Log last age    : {log_age:.0f}s")
        else:
            print(f"    Log last age    : NOT FOUND / NO TIMESTAMP")

        # Recommendation
        rec = r["recommendation"]
        note = r.get("protected_note", "")
        if note:
            print(f"    Recommendation  : {rec.upper()} - {note}")
        else:
            print(f"    Recommendation  : {rec.upper()}")

    print(f"\n{sep}")
    print("  SUMMARY")
    print(sep)
    stale_svcs = [r["service"] for r in results if r["is_stale"]]
    restart_candidates = [
        r["service"]
        for r in results
        if r["recommendation"] == "restart_candidate"
    ]
    print(f"  Stale services   : {stale_svcs or 'none'}")
    print(f"  Restart candidates (anti_entropy/wisdom_synthesiser only): {restart_candidates or 'none'}")
    print(f"\n  NOTE: write_service is PROTECTED - diagnostic only.")
    print(sep)


def run() -> list[dict[str, Any]]:
    """Run the full diagnostic and return structured results."""
    logger.info("Starting stale internal services diagnostic v2")
    print("\n=== STALE INTERNAL SERVICES DIAGNOSTIC v2 ===")
    print(f"Target staleness: {STALE_THRESHOLD_MINUTES} minutes (4h37m)")
    print(f"Target services: {list(SERVICES.keys())}\n")

    # 0. Verify write_service is reachable
    ws_status = check_write_service_reachable()
    print(f"[WS] write_service reachable: {ws_status['reachable']}")
    if ws_status["reachable"]:
        print(f"[WS] Response time: {ws_status.get('response_time_ms')}ms")
    else:
        print(f"[WS] Error: {ws_status.get('error')}")

    # 1. Diagnose each service
    results = []
    for name, cfg in SERVICES.items():
        logger.info("Diagnosing service: %s", name)
        result = diagnose_service(name, cfg)
        results.append(result)

    # 2. Print human-readable report
    print_report(results)

    # 3. Write diagnostic record
    wrote = write_diagnostic_record(results)
    logger.info("Diagnostic record written: %s", wrote)

    # 4. Return for programmatic consumption
    return results


if __name__ == "__main__":
    results = run()
    # Exit 0 if no restart candidates, else exit 1 to signal action needed
    restart_candidates = [
        r for r in results if r["recommendation"] == "restart_candidate"
    ]
    if restart_candidates:
        print(f"\nACTION NEEDED: restart candidates detected: {restart_candidates}")
    else:
        print("\nNo restart candidates - all services either healthy or protected.")
    raise SystemExit(0 if not restart_candidates else 1)
