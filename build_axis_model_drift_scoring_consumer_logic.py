import os
import sys
import time
import signal
import logging
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests

SERVICE_NAME = "axis_model_drift_scoring_consumer"
SERVICE_PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

DRIFT_SCORE_TABLE = "axis_model_drift_scores"
INPUT_TABLE = "axis_drift_events"
OUTPUT_TABLE = "mcp_signal_scores"

POLL_SECS = 30
BATCH_SIZE = 100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> Optional[Dict]:
    payload = {"table": table, "rows": rows, "wait": wait}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("ws_write failed for table %s: %s", table, e)
        return None


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    payload = {"sql": sql}
    try:
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return None


def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            logger.warning("Process %d is already running. Exiting.", old_pid)
            return False
        except (ValueError, OSError):
            logger.info("Stale PID file found. Removing.")
            os.remove(PID_FILE)
    pid = os.getpid()
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    logger.info("Acquired PID file: %s (pid=%d)", PID_FILE, pid)
    return True


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            logger.info("Removed PID file: %s", PID_FILE)
    except Exception as e:
        logger.warning("Failed to remove PID file: %s", e)


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    logger.info("Received %s, shutting down gracefully.", sig_name)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: Optional[Dict] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": json.dumps(meta) if meta else "{}",
    }
    ws_write("service_health", [row])


def ensure_tables() -> bool:
    drift_sql = f"""
    CREATE TABLE IF NOT EXISTS {DRIFT_SCORE_TABLE} (
        server_id VARCHAR,
        model_version VARCHAR,
        drift_score DOUBLE,
        drift_direction VARCHAR,
        feature_axis VARCHAR,
        computed_at TIMESTAMPTZ,
        evidence_json VARCHAR
    )
    """
    if not ws_execute(drift_sql):
        return False

    signal_sql = f"""
    CREATE TABLE IF NOT EXISTS {OUTPUT_TABLE} (
        server_id VARCHAR,
        signal_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        computed_at TIMESTAMPTZ
    )
    """
    return ws_execute(signal_sql)


def get_pending_drift_events(batch_size: int = BATCH_SIZE) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT server_id, model_version, drift_score, drift_direction,
           feature_axis, event_timestamp, evidence_json
    FROM {INPUT_TABLE}
    WHERE processed = FALSE OR processed IS NULL
    ORDER BY event_timestamp ASC
    LIMIT {batch_size}
    """
    rows = ws_query(sql)
    return rows if rows is not None else []


def mark_events_processed(server_ids: List[str], event_timestamps: List[str]) -> bool:
    if not server_ids:
        return True
    placeholders = ", ".join([f"('{sid}', '{ts}')" for sid, ts in zip(server_ids, event_timestamps)])
    sql = f"""
    UPDATE {INPUT_TABLE}
    SET processed = TRUE
    WHERE (server_id, event_timestamp) IN (VALUES {placeholders})
    """
    return ws_execute(sql)


def compute_drift_signal(server_id: str, drift_score: float, drift_direction: str, feature_axis: str, evidence: Dict) -> Dict[str, Any]:
    base_score = min(abs(drift_score) * 100.0, 100.0)
    if drift_direction == "increasing":
        direction_penalty = 0.0
    elif drift_direction == "decreasing":
        direction_penalty = -5.0
    else:
        direction_penalty = 0.0

    axis_weights = {
        "security": 1.5,
        "trust": 1.4,
        "behaviour": 1.3,
        "metadata": 1.0,
        "default": 1.0,
    }
    axis_weight = axis_weights.get(feature_axis, axis_weights["default"])
    final_score = max(0.0, min(100.0, (base_score + direction_penalty) * axis_weight / 1.5))

    signal_name = f"model_drift_{feature_axis}"

    evidence_blob = {
        "drift_score": drift_score,
        "drift_direction": drift_direction,
        "feature_axis": feature_axis,
        "model_version": evidence.get("model_version", "unknown"),
        "base_score": base_score,
        "axis_weight": axis_weight,
        "final_score": final_score,
        "computed_at": utc_now_iso(),
    }
    return {
        "server_id": server_id,
        "signal_name": signal_name,
        "score": round(final_score, 4),
        "evidence": json.dumps(evidence_blob),
        "computed_at": utc_now_iso(),
    }


def write_drift_scores(scores: List[Dict[str, Any]]) -> bool:
    if not scores:
        return True
    rows = [{"server_id": s["server_id"], "model_version": s.get("model_version", "unknown"),
             "drift_score": s["score"], "drift_direction": s.get("drift_direction", "unknown"),
             "feature_axis": s.get("feature_axis", "default"), "computed_at": utc_now_iso(),
             "evidence_json": s.get("evidence", "{}")} for s in scores]
    return ws_write(DRIFT_SCORE_TABLE, rows) is not None


def write_signal_scores(scores: List[Dict[str, Any]]) -> bool:
    if not scores:
        return True
    rows = [{"server_id": s["server_id"], "signal_name": s["signal_name"],
             "score": s["score"], "evidence": s["evidence"],
             "computed_at": s["computed_at"]} for s in scores]
    return ws_write(OUTPUT_TABLE, rows) is not None


def cycle() -> Dict[str, int]:
    results = {"events_seen": 0, "scores_computed": 0, "writes_ok": 0, "errors": 0}
    try:
        events = get_pending_drift_events(BATCH_SIZE)
        results["events_seen"] = len(events)
        if not events:
            logger.debug("No pending drift events found.")
            return results

        logger.info("Processing %d pending drift events.", len(events))

        signal_scores: List[Dict[str, Any]] = []
        drift_score_records: List[Dict[str, Any]] = []
        server_ids: List[str] = []
        event_timestamps: List[str] = []

        for event in events:
            try:
                server_id = str(event.get("server_id", ""))
                if not server_id:
                    continue

                drift_score = float(event.get("drift_score", 0.0))
                drift_direction = str(event.get("drift_direction", "unknown"))
                feature_axis = str(event.get("feature_axis", "default"))
                evidence_raw = event.get("evidence_json", "{}")
                try:
                    evidence = json.loads(evidence_raw) if isinstance(evidence_raw, str) else (evidence_raw or {})
                except Exception:
                    evidence = {}

                signal_record = compute_drift_signal(server_id, drift_score, drift_direction, feature_axis, evidence)
                signal_scores.append(signal_record)

                drift_score_records.append({
                    "server_id": server_id,
                    "model_version": str(event.get("model_version", "unknown")),
                    "score": signal_record["score"],
                    "drift_direction": drift_direction,
                    "feature_axis": feature_axis,
                    "evidence": signal_record["evidence"],
                })

                server_ids.append(server_id)
                event_timestamps.append(str(event.get("event_timestamp", utc_now_iso())))
                results["scores_computed"] += 1

            except Exception as e:
                logger.warning("Failed to process event for server %s: %s",
                               event.get("server_id", "?"), e)
                results["errors"] += 1

        if signal_scores:
            if write_signal_scores(signal_scores):
                results["writes_ok"] += len(signal_scores)
            if write_drift_scores(drift_score_records):
                results["writes_ok"] += len(drift_score_records)

        if server_ids:
            if mark_events_processed(server_ids, event_timestamps):
                logger.info("Marked %d events as processed.", len(server_ids))
            else:
                logger.warning("Failed to mark events as processed.")

    except Exception as e:
        logger.error("Cycle error: %s", e)
        results["errors"] += 1
    return results


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        sys.exit(1)

    logger.info("Starting %s daemon.", SERVICE_NAME)

    if not ensure_tables():
        logger.error("Failed to ensure required tables. Exiting.")
        remove_pid_file()
        sys.exit(1)

    logger.info("Tables verified. Entering main loop (poll every %ds).", POLL_SECS)

    while True:
        try:
            start = time.time()
            result = cycle()
            elapsed = time.time() - start
            logger.info("Cycle complete: events=%d scores=%d writes=%d errors=%d elapsed=%.2fs",
                        result["events_seen"], result["scores_computed"],
                        result["writes_ok"], result["errors"], elapsed)
            send_heartbeat(status="running", meta={"last_cycle": utc_now_iso(), "elapsed": round(elapsed, 3)})
        except Exception as e:
            logger.error("Cycle failed: %s", e)
            send_heartbeat(status="error", meta={"error": str(e)})

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()