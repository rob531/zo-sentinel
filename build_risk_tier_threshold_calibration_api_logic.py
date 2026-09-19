import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "risk_tier_threshold_calibration_api"
PORT = 8785
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

app = FastAPI()

THRESHOLD_TABLE = "risk_tier_thresholds"
AUDIT_TABLE = "threshold_calibration_audit"


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_URL, json={"sql": sql}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_URL, json={"sql": sql}, timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {sql[:100]}... Error: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    threshold_create = f"""
    CREATE TABLE IF NOT EXISTS {THRESHOLD_TABLE} (
        threshold_id VARCHAR PRIMARY KEY,
        tier_name VARCHAR NOT NULL UNIQUE,
        min_score DOUBLE NOT NULL,
        max_score DOUBLE,
        description VARCHAR,
        calibrated_at TIMESTAMPTZ,
        calibrated_by VARCHAR,
        is_active BOOLEAN DEFAULT TRUE
    )
    """
    ws_execute(threshold_create)

    audit_create = f"""
    CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
        audit_id VARCHAR PRIMARY KEY,
        threshold_id VARCHAR,
        tier_name VARCHAR NOT NULL,
        old_min_score DOUBLE,
        new_min_score DOUBLE NOT NULL,
        old_max_score DOUBLE,
        new_max_score DOUBLE,
        changed_at TIMESTAMPTZ NOT NULL,
        changed_by VARCHAR,
        reason VARCHAR,
        approved_by VARCHAR,
        approved_at TIMESTAMPTZ
    )
    """
    ws_execute(audit_create)
    log.info("Threshold calibration tables verified")


def get_current_thresholds() -> list:
    sql = f"SELECT threshold_id, tier_name, min_score, max_score, description, calibrated_at, calibrated_by, is_active FROM {THRESHOLD_TABLE} WHERE is_active = TRUE ORDER BY min_score DESC"
    return ws_query(sql)


def get_threshold_by_tier(tier_name: str) -> dict:
    sql = f"SELECT threshold_id, tier_name, min_score, max_score, description, calibrated_at, calibrated_by, is_active FROM {THRESHOLD_TABLE} WHERE tier_name = '{tier_name}' AND is_active = TRUE"
    rows = ws_query(sql)
    return rows[0] if rows else None


def compute_threshold_id(tier_name: str, min_score: float) -> str:
    import hashlib
    raw = f"{tier_name}:{min_score}:{utc_now_iso()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def validate_threshold_range(tier_name: str, min_score: float, max_score: float = None) -> tuple:
    valid_tiers = ["KNOWN_THREAT", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "AMBER_UNVERIFIED", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]
    if tier_name not in valid_tiers:
        return False, f"Invalid tier_name. Must be one of: {', '.join(valid_tiers)}"
    if not (0.0 <= min_score <= 1.0):
        return False, "min_score must be between 0.0 and 1.0"
    if max_score is not None:
        if not (0.0 <= max_score <= 1.0):
            return False, "max_score must be between 0.0 and 1.0"
        if max_score <= min_score:
            return False, "max_score must be greater than min_score"
    overlapping = check_overlapping_thresholds(tier_name, min_score, max_score)
    if overlapping:
        return False, "Proposed threshold overlaps with existing threshold for this tier"
    return True, "Valid"


def check_overlapping_thresholds(tier_name: str, min_score: float, max_score: float = None) -> bool:
    sql = f"SELECT threshold_id, min_score, max_score FROM {THRESHOLD_TABLE} WHERE tier_name = '{tier_name}' AND is_active = TRUE"
    existing = ws_query(sql)
    for row in existing:
        existing_min = row.get("min_score", 0)
        existing_max = row.get("max_score", 1.0)
        if max_score is None:
            if min_score >= existing_min and min_score <= existing_max:
                return True
        else:
            if not (max_score < existing_min or min_score > existing_max):
                return True
    return False


def update_threshold(
    tier_name: str,
    new_min_score: float,
    new_max_score: float = None,
    calibrated_by: str = "system",
    reason: str = None,
) -> tuple:
    existing = get_threshold_by_tier(tier_name)
    if not existing:
        return False, f"No active threshold found for tier {tier_name}"
    old_min = existing.get("min_score")
    old_max = existing.get("max_score")
    valid, msg = validate_threshold_range(tier_name, new_min_score, new_max_score)
    if not valid:
        return False, msg
    threshold_id = compute_threshold_id(tier_name, new_min_score)
    now = utc_now_iso()
    deactivate_sql = f"UPDATE {THRESHOLD_TABLE} SET is_active = FALSE WHERE tier_name = '{tier_name}' AND is_active = TRUE"
    ws_execute(deactivate_sql)
    insert_sql = f"""
    INSERT INTO {THRESHOLD_TABLE} (threshold_id, tier_name, min_score, max_score, description, calibrated_at, calibrated_by, is_active)
    VALUES ('{threshold_id}', '{tier_name}', {new_min_score}, {new_max_score if new_max_score else 'NULL'}, '{reason or ''}', '{now}', '{calibrated_by}', TRUE)
    """
    ws_execute(insert_sql)
    audit_id = compute_threshold_id(f"audit_{tier_name}", now)
    audit_sql = f"""
    INSERT INTO {THRESHOLD_TABLE.replace('thresholds', 'calibration_audit')} (audit_id, tier_name, old_min_score, new_min_score, old_max_score, new_max_score, changed_at, changed_by, reason)
    VALUES ('{audit_id}', '{tier_name}', {old_min}, {new_min_score}, {old_max}, {new_max_score if new_max_score else 'NULL'}, '{now}', '{calibrated_by}', '{reason or ''}')
    """.replace(f"{THRESHOLD_TABLE.replace('thresholds', 'calibration_audit')}", AUDIT_TABLE)
    ws_execute(audit_sql)
    log.info(f"Updated threshold for {tier_name}: {old_min}->{new_min_score}")
    return True, f"Threshold updated for {tier_name}"


def reset_to_defaults() -> bool:
    defaults = [
        {"tier_name": "KNOWN_THREAT", "min_score": 0.0, "max_score": 0.1, "description": "Confirmed malicious MCP servers"},
        {"tier_name": "HIGH_RISK_ISOLATED", "min_score": 0.1, "max_score": 0.3, "description": "High risk with limited attestations"},
        {"tier_name": "CAUTION_LIMITED", "min_score": 0.3, "max_score": 0.5, "description": "Moderate risk requiring caution"},
        {"tier_name": "AMBER_UNVERIFIED", "min_score": 0.5, "max_score": 0.7, "description": "Unverified sources or incomplete signals"},
        {"tier_name": "TRUSTED_RESEARCH", "min_score": 0.7, "max_score": 0.9, "description": "Trusted with research backing"},
        {"tier_name": "ENTERPRISE_CONTROLLED", "min_score": 0.9, "max_score": 1.0, "description": "Enterprise-grade controlled MCPs"},
    ]
    deactivate_sql = f"UPDATE {THRESHOLD_TABLE} SET is_active = FALSE WHERE is_active = TRUE"
    ws_execute(deactivate_sql)
    now = utc_now_iso()
    for d in defaults:
        threshold_id = compute_threshold_id(d["tier_name"], d["min_score"])
        insert_sql = f"""
        INSERT INTO {THRESHOLD_TABLE} (threshold_id, tier_name, min_score, max_score, description, calibrated_at, calibrated_by, is_active)
        VALUES ('{threshold_id}', '{d['tier_name']}', {d['min_score']}, {d['max_score']}, '{d['description']}', '{now}', 'system_reset', TRUE)
        """
        ws_execute(insert_sql)
    log.info("Reset all thresholds to defaults")
    return True


def get_threshold_audit_history(tier_name: str = None, limit: int = 50) -> list:
    sql = f"SELECT audit_id, threshold_id, tier_name, old_min_score, new_min_score, old_max_score, new_max_score, changed_at, changed_by, reason, approved_by, approved_at FROM {AUDIT_TABLE}"
    if tier_name:
        sql += f" WHERE tier_name = '{tier_name}'"
    sql += f" ORDER BY changed_at DESC LIMIT {limit}"
    return ws_query(sql)


def apply_calibration(calibration_id: str, approved_by: str = "system") -> bool:
    sql = f"SELECT * FROM {AUDIT_TABLE} WHERE audit_id = '{calibration_id}'"
    rows = ws_query(sql)
    if not rows:
        return False
    record = rows[0]
    update_threshold(
        tier_name=record["tier_name"],
        new_min_score=record["new_min_score"],
        new_max_score=record.get("new_max_score"),
        calibrated_by=record.get("changed_by", "system"),
        reason=f"Approved by {approved_by}: {record.get('reason', '')}",
    )
    approve_sql = f"UPDATE {AUDIT_TABLE} SET approved_by = '{approved_by}', approved_at = '{utc_now_iso()}' WHERE audit_id = '{calibration_id}'"
    ws_execute(approve_sql)
    return True


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/api/thresholds")
def list_thresholds():
    thresholds = get_current_thresholds()
    return {"thresholds": thresholds, "count": len(thresholds)}


@app.get("/api/thresholds/{tier_name}")
def get_threshold(tier_name: str):
    threshold = get_threshold_by_tier(tier_name)
    if not threshold:
        return {"error": f"No threshold found for tier {tier_name}"}, 404
    return threshold


@app.post("/api/thresholds/{tier_name}")
def set_threshold(tier_name: str, min_score: float, max_score: float = None, calibrated_by: str = "api", reason: str = None):
    success, msg = update_threshold(tier_name, min_score, max_score, calibrated_by, reason)
    if success:
        return {"success": True, "message": msg}
    return {"success": False, "error": msg}, 400


@app.post("/api/thresholds/reset")
def reset_thresholds():
    success = reset_to_defaults()
    return {"success": success, "message": "Thresholds reset to defaults" if success else "Reset failed"}


@app.get("/api/thresholds/audit")
def list_audit_history(tier_name: str = None, limit: int = 50):
    history = get_threshold_audit_history(tier_name, limit)
    return {"audit_history": history, "count": len(history)}


@app.post("/api/thresholds/audit/{audit_id}/approve")
def approve_calibration(audit_id: str, approved_by: str = "api"):
    success = apply_calibration(audit_id, approved_by)
    return {"success": success, "message": "Calibration approved and applied" if success else "Approval failed"}


@app.get("/api/thresholds/schema")
def get_threshold_schema():
    return {
        "tiers": ["KNOWN_THREAT", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "AMBER_UNVERIFIED", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"],
        "score_range": {"min": 0.0, "max": 1.0},
        "description": "Risk tier threshold calibration API for managing MCP server risk classification boundaries",
    }


def send_heartbeat() -> None:
    now = utc_now_iso()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": now,
        "status": "running",
        "meta": "{}",
    }
    ws_write("service_health", [row])


def check_single_instance() -> None:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        import os
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found (PID {old_pid}). Removing.")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    log.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def cycle() -> None:
    ensure_tables()
    send_heartbeat()


def run() -> None:
    import os
    import signal

    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_tables()
    cycle()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()