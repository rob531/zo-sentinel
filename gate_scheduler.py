#!/usr/bin/env python3
"""
gate_scheduler.py -- Persistent daemon that invokes run_gates_periodic.py
on a fixed interval. Replaces cron (which isn't available in this container).

Behavior:
  - Runs run_gates_periodic.py immediately on startup (catch regressions fast
    after a restart rather than waiting a full interval)
  - Then sleeps INTERVAL_SEC and re-invokes, forever
  - Emits its own heartbeat to service_health as 'gate_scheduler' independent
    of the gate run's 'gate_orchestrator' heartbeat -- so you can tell the
    difference between "scheduler dead" and "gate run stuck"
  - Logs each invocation's summary line
  - Never crashes on gate failure (expected; exit codes 0 and 1 are normal)
  - Crashes only on programmer error, which supervisord will auto-restart

Philosophy (Option A from design doc):
  - Gates are advisory. Fail -> log, keep scheduling. No governance events,
    no directive generation triggers. Humans grep gate_cron.log when they
    want to know.
  - Structured output format leaves room to upgrade to governance later
    without changing the daemon.

Intended to run under supervisord as 'gate_scheduler'.
Heartbeats every 60s so stale detection is quick.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import requests

# ---- Config ------------------------------------------------------------------

INTERVAL_SEC    = int(os.environ.get("GATE_INTERVAL_SEC", 21600))   # 6h default
HEARTBEAT_SEC   = 60
SERVICE_NAME    = "gate_scheduler"
PERIODIC_SCRIPT = "/home/workspace/zo_sentinel/tests/gates/run_gates_periodic.py"
HEARTBEAT_URL   = "http://127.0.0.1:8772/write"
MAX_RUN_SEC     = 360   # modest headroom over run_gates_periodic's own 300s cap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gate_scheduler] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(SERVICE_NAME)

_stop_requested = threading.Event()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(status: str = "healthy", note: str = "") -> None:
    """Best-effort. Never raises."""
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
        pass


def _heartbeat_loop() -> None:
    """Background thread. Beats every HEARTBEAT_SEC regardless of cycle state."""
    while not _stop_requested.is_set():
        _heartbeat(status="healthy")
        _stop_requested.wait(HEARTBEAT_SEC)


def _run_once() -> int:
    """Invoke run_gates_periodic.py once, return its exit code.

    Exit code semantics (inherited from run_gates_periodic):
        0  all gates passed
        1  one or more gates failed (expected; not a scheduler problem)
        2  infra problem (db lock, missing bootstrap, etc.)
        3  overlap with prior run
    """
    started = time.monotonic()
    log.info("Invoking %s", PERIODIC_SCRIPT)
    try:
        proc = subprocess.run(
            ["python3", PERIODIC_SCRIPT],
            capture_output=True,
            text=True,
            timeout=MAX_RUN_SEC,
        )
        rc = proc.returncode
        # run_gates_periodic emits one 'RUN_SUMMARY ts=...' line we propagate
        summary = ""
        for line in (proc.stdout or "").splitlines():
            if line.startswith("RUN_SUMMARY"):
                summary = line.strip()
                break
        dur_ms = int((time.monotonic() - started) * 1000)
        if summary:
            log.info("finished rc=%d dur_ms=%d %s", rc, dur_ms, summary)
        else:
            log.info("finished rc=%d dur_ms=%d (no RUN_SUMMARY emitted)", rc, dur_ms)
        if proc.stderr:
            # Don't spam logs; first 200 chars is enough for forensics
            log.warning("stderr[:200]: %s", proc.stderr[:200].replace("\n", " | "))
        return rc
    except subprocess.TimeoutExpired:
        log.error("TIMEOUT after %ds invoking periodic runner", MAX_RUN_SEC)
        return 2
    except Exception as e:
        log.error("invoke failed: %s", e)
        return 2


def _handle_signal(signum, frame):
    log.info("received signal %d; requesting stop", signum)
    _stop_requested.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    log.info("=" * 60)
    log.info("ZO-SENTINEL Gate Scheduler v1.0")
    log.info("  Invokes:  %s", PERIODIC_SCRIPT)
    log.info("  Interval: %ds (%.1fh)", INTERVAL_SEC, INTERVAL_SEC / 3600)
    log.info("  HB every: %ds", HEARTBEAT_SEC)
    log.info("=" * 60)

    # Quick sanity: the periodic runner must exist and parse
    if not os.path.exists(PERIODIC_SCRIPT):
        log.error("periodic script missing: %s", PERIODIC_SCRIPT)
        return 2

    # Start heartbeat thread
    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name="hb")
    hb_thread.start()

    # Immediate run at startup so restart -> fresh signal in under 60s
    _run_once()

    # Periodic loop
    while not _stop_requested.is_set():
        # Interruptible sleep
        _stop_requested.wait(INTERVAL_SEC)
        if _stop_requested.is_set():
            break
        _run_once()

    log.info("clean shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())