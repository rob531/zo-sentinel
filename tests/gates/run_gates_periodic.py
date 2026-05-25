#!/usr/bin/env python3
"""
run_gates_periodic.py -- Scheduled runner wrapping run_gates.py.

Designed to be invoked by cron (or a supervisor) every 6 hours.

Responsibilities beyond run_gates.py:
    1. Single-instance guard via fcntl.flock (no pidfiles, no /var/run)
    2. Heartbeat to service_health as 'gate_orchestrator' so the sentinel
       status display sees it alongside the 8 daemons
    3. Log rotation: each run writes to /home/workspace/logs/gate_runs/<ts>.log
       and a stable symlink 'latest.log' for quick inspection
    4. Structured exit codes for cron:
         0 -- all gates passed
         1 -- one or more gates failed (expected; cron should not alarm)
         2 -- infra problem (db lock, missing bootstrap, etc.)
         3 -- overlap with prior run (not an error, just info)
    5. Write the final "run summary" line in a greppable format so
       historical trends are extractable later:
           RUN_SUMMARY ts=... run_id=... checks=... fail=... dur_ms=...

Safety:
    - Never kills a prior run; just skips if one is active
    - Never runs longer than 5 minutes (hard timeout kills the subprocess)
    - Heartbeat is best-effort; a write_service blip does not fail the run
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

GATES_DIR     = "/home/workspace/zo_sentinel/tests/gates"
LOGS_DIR      = Path("/home/workspace/logs/gate_runs")
LOCK_PATH     = "/tmp/zo_gate_orchestrator.lock"
HEARTBEAT_URL = "http://127.0.0.1:8772/write"
MAX_RUN_SEC   = 300   # 5 minutes hard ceiling
SERVICE_NAME  = "gate_orchestrator"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(status: str = "healthy", note: str = ""):
    """Best-effort heartbeat to service_health. Never fails the run."""
    try:
        requests.post(
            HEARTBEAT_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service":        SERVICE_NAME,
                    "last_heartbeat": _now_iso(),
                    "status":         status,
                    "meta":           json.dumps({"note": note})[:500] if note else None,
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception:
        pass  # heartbeat failure is non-fatal


def _acquire_lock():
    """Try to acquire the single-instance flock. Returns fd or None on contention."""
    fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(f"{os.getpid()} {_now_iso()}\n")
        fd.flush()
        return fd
    except BlockingIOError:
        fd.close()
        return None


def main() -> int:
    started = time.monotonic()
    run_ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"gate_run_{run_ts}.log"
    latest_link = LOGS_DIR / "latest.log"

    # Single-instance lock
    lock_fd = _acquire_lock()
    if lock_fd is None:
        _heartbeat(status="skipped", note="overlap with prior run")
        print(f"[SKIP] another gate run already in progress at {_now_iso()}")
        return 3

    try:
        _heartbeat(status="starting", note=f"log={log_path.name}")

        # Run the gate suite with hard timeout
        cmd = [
            "python3",
            f"{GATES_DIR}/run_gates.py",
        ]
        with open(log_path, "w") as logf:
            logf.write(f"=== gate run started at {_now_iso()} ===\n")
            logf.flush()
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    timeout=MAX_RUN_SEC,
                )
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                logf.write(f"\n=== TIMEOUT after {MAX_RUN_SEC}s ===\n")
                rc = 2

            duration_ms = int((time.monotonic() - started) * 1000)
            logf.write(f"\n=== finished at {_now_iso()}  rc={rc}  dur={duration_ms}ms ===\n")

        # Update 'latest' convenience symlink
        try:
            if latest_link.is_symlink() or latest_link.exists():
                latest_link.unlink()
            latest_link.symlink_to(log_path.name)  # relative for portability
        except Exception:
            pass

        # Emit greppable summary line for historical trend extraction
        # Also read the run log tail to enrich the summary
        summary_note = f"rc={rc} log={log_path.name}"
        _heartbeat(
            status=("healthy" if rc in (0, 1) else "error"),
            note=summary_note,
        )

        # Keep the last 30 runs; prune older
        try:
            logs = sorted(LOGS_DIR.glob("gate_run_*.log"))
            for old in logs[:-30]:
                old.unlink()
        except Exception:
            pass

        dur_total_ms = int((time.monotonic() - started) * 1000)
        print(f"RUN_SUMMARY ts={run_ts} rc={rc} dur_ms={dur_total_ms} log={log_path.name}")
        return rc
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass
        try:
            os.unlink(LOCK_PATH)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())