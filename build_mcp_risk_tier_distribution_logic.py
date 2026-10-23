import os
import sys
import time
import logging
import signal
import requests
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "mcp_risk_tier_distribution_logic"
SERVICE_PORT = 8790
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(SERVICE_NAME)

_process_running = True


def signal_handler(signum, frame):
    global _process_running
    sig_name = signal.Signals(signum).name
    log.warning(f"Received {sig_name}, shutting down gracefully")
    _process_running = False


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def check_single_instance():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log.error(f"Another instance is running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except (ProcessLookupError, ValueError, PermissionError):
            log.warning("Stale PID file found, removing it.")
            remove_pid_file()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ws_query(sql):
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_query failed: {e} | SQL: {sql[:200]}")
        return None


def ws_write(table, rows):
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_write failed: {e} | table={table}")
        return None


def ws_execute(sql):
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_execute failed: {e} | SQL: {sql[:200]}")
        return None


def send_heartbeat(status="running", meta=None):
    meta = meta or {}
    meta["ts"] = utc_now_iso()
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "status": status,
            "last_heartbeat": utc_now_iso(),
            "meta": str(meta)
        }])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def get_risk_tier_distribution():
    sql = """
    SELECT
        risk_tier,
        COUNT(*) AS server_count,
        ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 2) AS pct
    FROM mcp_risk_register
    GROUP BY risk_tier
    ORDER BY
        CASE risk_tier
            WHEN 'CRITICAL' THEN 1
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            WHEN 'LOW' THEN 4
            WHEN 'INFO' THEN 5
            ELSE 9
        END
    """
    result = ws_query(sql)
    if result and isinstance(result, dict) and "rows" in result:
        return result["rows"]
    if result and isinstance(result, list):
        return result
    return []


def get_verdict_distribution():
    sql = """
    SELECT
        verdict,
        COUNT(*) AS server_count
    FROM mcp_server_registry
    GROUP BY verdict
    ORDER BY server_count DESC
    """
    result = ws_query(sql)
    if result and isinstance(result, dict) and "rows" in result:
        return result["rows"]
    if result and isinstance(result, list):
        return result
    return []


def get_trust_score_bands():
    sql = """
    SELECT
        CASE
            WHEN trust_score >= 90 THEN '90-100'
            WHEN trust_score >= 80 THEN '80-89'
            WHEN trust_score >= 70 THEN '70-79'
            WHEN trust_score >= 60 THEN '60-69'
            WHEN trust_score >= 50 THEN '50-59'
            WHEN trust_score >= 40 THEN '40-49'
            WHEN trust_score >= 30 THEN '30-39'
            WHEN trust_score >= 20 THEN '20-29'
            WHEN trust_score >= 10 THEN '10-19'
            ELSE '0-9'
        END AS score_band,
        COUNT(*) AS server_count
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    GROUP BY score_band
    ORDER BY score_band DESC
    """
    result = ws_query(sql)
    if result and isinstance(result, dict) and "rows" in result:
        return result["rows"]
    if result and isinstance(result, list):
        return result
    return []


def get_top_high_risk_servers(limit=20):
    sql = f"""
    SELECT
        r.server_id,
        r.name,
        r.url,
        r.verdict,
        r.trust_score,
        rk.risk_tier,
        rk.threat_count
    FROM mcp_server_registry r
    JOIN mcp_risk_register rk ON r.server_id = rk.server_id
    WHERE rk.risk_tier IN ('CRITICAL', 'HIGH')
    ORDER BY
        CASE rk.risk_tier WHEN 'CRITICAL' THEN 0 ELSE 1 END,
        rk.threat_count DESC
    LIMIT {limit}
    """
    result = ws_query(sql)
    if result and isinstance(result, dict) and "rows" in result:
        return result["rows"]
    if result and isinstance(result, list):
        return result
    return []


def get_risk_tier_trend():
    sql = """
    SELECT
        DATE_TRUNC('day', computed_at) AS day,
        risk_tier,
        COUNT(*) AS server_count
    FROM mcp_risk_register
    GROUP BY day, risk_tier
    ORDER BY day DESC, risk_tier
    LIMIT 100
    """
    result = ws_query(sql)
    if result and isinstance(result, dict) and "rows" in result:
        return result["rows"]
    if result and isinstance(result, list):
        return result
    return []


def compute_distribution_snapshot():
    ts = utc_now_iso()
    snapshot_id = f"rtds_{ts.replace(':', '').replace('-', '').replace('Z', '')}"

    tier_dist = get_risk_tier_distribution()
    verdict_dist = get_verdict_distribution()
    score_bands = get_trust_score_bands()
    top_risks = get_top_high_risk_servers()
    trend = get_risk_tier_trend()

    total_risks = sum(row.get("server_count", 0) for row in tier_dist)
    total_verdicts = sum(row.get("server_count", 0) for row in verdict_dist)

    summary = {
        "snapshot_id": snapshot_id,
        "computed_at": ts,
        "total_risk_entries": total_risks,
        "total_registry_entries": total_verdicts,
        "tier_distribution": tier_dist,
        "verdict_distribution": verdict_dist,
        "trust_score_bands": score_bands,
        "top_high_risk_servers": top_risks,
        "risk_trend": trend
    }

    return summary


def write_distribution_snapshot(snapshot):
    table = "risk_tier_distribution_snapshots"
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table} (
        snapshot_id VARCHAR PRIMARY KEY,
        computed_at TIMESTAMPTZ,
        total_risk_entries BIGINT,
        total_registry_entries BIGINT,
        tier_distribution_json JSON,
        verdict_distribution_json JSON,
        trust_score_bands_json JSON,
        top_high_risk_servers_json JSON,
        risk_trend_json JSON,
        meta JSON
    )
    """
    ws_execute(create_sql)

    insert_sql = f"""
    INSERT INTO {table} (
        snapshot_id, computed_at, total_risk_entries, total_registry_entries,
        tier_distribution_json, verdict_distribution_json, trust_score_bands_json,
        top_high_risk_servers_json, risk_trend_json, meta
    ) VALUES (
        '{snapshot["snapshot_id"]}',
        '{snapshot["computed_at"]}',
        {snapshot["total_risk_entries"]},
        {snapshot["total_registry_entries"]},
        '{json.dumps(snapshot["tier_distribution"])}',
        '{json.dumps(snapshot["verdict_distribution"])}',
        '{json.dumps(snapshot["trust_score_bands"])}',
        '{json.dumps(snapshot["top_high_risk_servers"])}',
        '{json.dumps(snapshot["risk_trend"])}',
        '{{}}'
    )
    ON CONFLICT (snapshot_id) DO NOTHING
    """
    result = ws_execute(insert_sql)
    if result:
        log.info(f"Snapshot {snapshot['snapshot_id']} written: {snapshot['total_risk_entries']} risk entries")
    return result


def cycle():
    log.info("Computing risk tier distribution snapshot...")
    try:
        snapshot = compute_distribution_snapshot()
        if snapshot:
            write_distribution_snapshot(snapshot)
            log.info(
                f"Cycle complete: {snapshot['total_risk_entries']} risk entries, "
                f"{snapshot['total_registry_entries']} registry entries"
            )
        else:
            log.warning("No snapshot data returned")
    except Exception as e:
        log.error(f"cycle() failed: {e}", exc_info=True)


def heartbeat_loop():
    log.info("Starting risk tier distribution heartbeat loop")
    while _process_running:
        try:
            send_heartbeat(status="running")
        except Exception as e:
            log.warning(f"Heartbeat failed: {e}")
        time.sleep(60)


import json


def run():
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        cycle()
    except Exception as e:
        log.error(f"Initial cycle failed: {e}", exc_info=True)

    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    log.info(f"{SERVICE_NAME} run loop started, cycling every 5 minutes")
    while _process_running:
        try:
            cycle()
        except Exception as e:
            log.error(f"Run loop error: {e}", exc_info=True)
        for _ in range(300):
            if not _process_running:
                break
            time.sleep(1)

    log.info(f"{SERVICE_NAME} shutting down")
    remove_pid_file()
    sys.exit(0)


if __name__ == "__main__":
    run()