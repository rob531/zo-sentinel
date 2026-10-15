#!/usr/bin/env python3
"""
Signal Drift Detector - Detects anomalous drift in MCP server signal scores over time.
Zo-Sentinel service module.
"""

import hashlib
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SERVICE_NAME = "signal_drift_detector"
PORT = None  # This is a daemon, not a FastAPI service
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
POLL_SECS = 300  # 5 minutes
DRIFT_THRESHOLD = 0.15  # 15% change threshold
LOOKBACK_CYCLES = 5  # Compare against this many previous cycles

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Write Service Helpers
# ---------------------------------------------------------------------------
def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service for SELECT statements."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list[dict[str, Any]]) -> None:
    """Write rows via write_service."""
    requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={"table": table, "rows": rows, "wait": True},
        timeout=30,
    )
    logger.debug("Wrote %d rows to %s", len(rows), table)


def ws_execute(sql: str) -> None:
    """Execute DDL/DML via write_service."""
    requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json={"sql": sql},
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Single Instance Guard
# ---------------------------------------------------------------------------
def check_single_instance() -> None:
    """Ensure only one instance runs at a time."""
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance already running (PID %d). Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            logger.info("Stale PID file removed (PID %d). Proceeding.", old_pid)
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file on exit."""
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT gracefully."""
    logger.info("Received signal %d, shutting down.", signum)
    remove_pid_file()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Signal Drift Logic
# ---------------------------------------------------------------------------
def compute_deterministic_id(server_id: str, signal_name: str) -> str:
    """Generate deterministic ID for drift alerts to ensure idempotent writes."""
    content = f"{server_id}:{signal_name}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:32]


def get_current_scores() -> list[dict[str, Any]]:
    """Fetch current signal scores from mcp_signal_scores table."""
    sql = """
        SELECT 
            server_id,
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        ORDER BY server_id, signal_name
    """
    return ws_query(sql)


def get_baseline_for_server(server_id: str, signal_name: str) -> dict[str, Any] | None:
    """Fetch the most recent baseline snapshot for a given server/signal pair."""
    sql = f"""
        SELECT 
            server_id,
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}' AND signal_name = '{signal_name}'
        ORDER BY scored_at DESC
        LIMIT 1
    """
    rows = ws_query(sql)
    return rows[0] if rows else None


def get_drift_alerts_table() -> list[dict[str, Any]]:
    """Fetch existing drift alert IDs to avoid duplicates."""
    sql = "SELECT alert_id FROM signal_drift_alerts"
    try:
        return ws_query(sql)
    except Exception:
        return []


def compute_score_drift(current: float, baseline: float) -> float:
    """Compute absolute drift between two scores."""
    if baseline == 0:
        return abs(current) if current != 0 else 0.0
    return abs(current - baseline) / abs(baseline)


def detect_drift() -> list[dict[str, Any]]:
    """Detect signal score drift across all servers."""
    alerts: list[dict[str, Any]] = []
    scores = get_current_scores()

    # Group by server_id + signal_name
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in scores:
        key = (row["server_id"], row["signal_name"])
        by_key.setdefault(key, []).append(row)

    existing_alert_ids = {r["alert_id"] for r in get_drift_alerts_table()}

    ts = datetime.now(timezone.utc).isoformat()

    for (server_id, signal_name), rows in by_key.items():
        if len(rows) < 2:
            continue

        # Sort by scored_at descending
        rows.sort(key=lambda x: x["scored_at"], reverse=True)
        current = rows[0]
        baseline = rows[-1]  # oldest in our window

        drift = compute_score_drift(current["score"], baseline["score"])

        if drift >= DRIFT_THRESHOLD:
            alert_id = compute_deterministic_id(server_id, signal_name)
            if alert_id in existing_alert_ids:
                logger.debug("Alert %s already exists, skipping.", alert_id)
                continue

            direction = "increased" if current["score"] > baseline["score"] else "decreased"
            evidence = (
                f"Signal '{signal_name}' for server '{server_id}' {direction} by "
                f"{drift:.1%} (baseline={baseline['score']:.3f}, current={current['score']:.3f})"
            )

            alerts.append({
                "alert_id": alert_id,
                "server_id": server_id,
                "signal_name": signal_name,
                "baseline_score": baseline["score"],
                "current_score": current["score"],
                "drift_pct": round(drift, 4),
                "direction": direction,
                "evidence": evidence,
                "detected_at": ts,
                "status": "open",
            })

    return alerts


def emit_drift_alerts(alerts: list[dict[str, Any]]) -> None:
    """Write drift alerts to the signal_drift_alerts table."""
    if not alerts:
        logger.info("No drift detected in this cycle.")
        return

    ws_write("signal_drift_alerts", alerts)
    logger.info(
        "Emitted %d drift alerts: %s",
        len(alerts),
        [a["alert_id"] for a in alerts],
    )


def ensure_alerts_table() -> None:
    """Create signal_drift_alerts table if it doesn't exist."""
    sql = """
        CREATE TABLE IF NOT EXISTS signal_drift_alerts (
            alert_id     VARCHAR PRIMARY KEY,
            server_id    VARCHAR,
            signal_name  VARCHAR,
            baseline_score DOUBLE,
            current_score  DOUBLE,
            drift_pct    DOUBLE,
            direction    VARCHAR,
            evidence     VARCHAR,
            detected_at  VARCHAR,
            status       VARCHAR DEFAULT 'open'
        )
    """
    try:
        ws_execute(sql)
        logger.info("signal_drift_alerts table ready.")
    except Exception as e:
        logger.warning("Table creation skipped (may already exist): %s", e)


def get_registry_trust_scores() -> list[dict[str, Any]]:
    """Fetch trust scores from mcp_server_registry for additional context."""
    sql = """
        SELECT 
            server_id,
            name,
            trust_score,
            verdict,
            last_seen = CAST('' AS VARCHAR) OR last_seen  -- alias for scan
        FROM mcp_server_registry
        WHERE trust_score IS NOT NULL
        ORDER BY server_id
        LIMIT 500
    """
    return ws_query(sql)


def send_heartbeat() -> None:
    """Write heartbeat to service_health table."""
    ts = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
    }])


# ---------------------------------------------------------------------------
# Cycle & Run Loop
# ---------------------------------------------------------------------------
def cycle() -> None:
    """Execute one drift detection cycle."""
    logger.info("Starting drift detection cycle.")
    try:
        ensure_alerts_table()
        alerts = detect_drift()
        emit_drift_alerts(alerts)
        send_heartbeat()
        logger.info("Drift detection cycle complete. Alerts: %d", len(alerts))
    except requests.RequestException as e:
        logger.error("WriteService request failed: %s", e)
    except Exception as e:
        logger.exception("Unexpected error in drift detection cycle: %s", e)


def run() -> None:
    """Main daemon loop."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("%s starting. PID=%d", SERVICE_NAME, signal.getpid())
    logger.info("Drift threshold: %.1%%, lookback cycles: %d", DRIFT_THRESHOLD * 100, LOOKBACK_CYCLES)

    while True:
        cycle()
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()