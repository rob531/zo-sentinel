import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "server_trust_summary"
PORT = 8786
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()],
)
log = logging.getLogger(SERVICE_NAME)


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e} | SQL: {sql[:200]}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e} | table: {table}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e} | SQL: {sql[:200]}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Already running as PID {old_pid}. Exiting.")
            sys.exit(1)
        except (OSError, ValueError):
            log.warning(f"Stale PID file {old_pid}, removing.")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    log.info(f"PID {os.getpid()} written to {PID_FILE}")


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta,
    }
    ws_write("service_health", [row])


def ensure_tables() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS server_trust_summary (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        url VARCHAR,
        description VARCHAR,
        trust_score DOUBLE,
        verdict VARCHAR,
        risk_tier VARCHAR,
        signal_breakdown JSON,
        attestation_count INTEGER,
        threat_count INTEGER,
        last_scanned TIMESTAMP,
        computed_at TIMESTAMP
    )
    """
    ws_execute(create_sql)
    log.info("server_trust_summary table ensured.")


def get_registry_record(server_id: str) -> dict:
    sql = f"SELECT server_id, name, url, description, trust_score, verdict, risk_tier, last_scanned FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    return rows[0] if rows else {}


def get_signal_scores(server_id: str) -> list:
    sql = f"SELECT signal_name, score, evidence FROM mcp_signal_scores WHERE server_id = '{server_id}'"
    return ws_query(sql)


def get_attestation_count(server_id: str) -> int:
    sql = f"SELECT COUNT(*) as cnt FROM mcp_attestations WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    return rows[0]["cnt"] if rows else 0


def get_threat_count(server_id: str) -> int:
    sql = f"SELECT COUNT(*) as cnt FROM mcp_threat_associations WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    return rows[0]["cnt"] if rows else 0


def compute_signal_breakdown(signal_scores: list) -> dict:
    breakdown = {}
    for row in signal_scores:
        breakdown[row["signal_name"]] = {
            "score": row.get("score", 0),
            "evidence": row.get("evidence", "")[:200] if row.get("evidence") else "",
        }
    return breakdown


def compute_trust_summary(server_id: str) -> dict:
    record = get_registry_record(server_id)
    if not record:
        return {}

    signal_scores = get_signal_scores(server_id)
    attestation_count = get_attestation_count(server_id)
    threat_count = get_threat_count(server_id)
    signal_breakdown = compute_signal_breakdown(signal_scores)

    return {
        "server_id": server_id,
        "name": record.get("name", ""),
        "url": record.get("url", ""),
        "description": record.get("description", ""),
        "trust_score": record.get("trust_score", 0.0),
        "verdict": record.get("verdict", "UNKNOWN"),
        "risk_tier": record.get("risk_tier", ""),
        "signal_breakdown": signal_breakdown,
        "attestation_count": attestation_count,
        "threat_count": threat_count,
        "last_scanned": record.get("last_scanned", ""),
        "computed_at": utc_now_iso(),
    }


def upsert_trust_summary(summary: dict) -> bool:
    if not summary:
        return False
    return ws_write("server_trust_summary", [summary])


def refresh_all_summaries(batch_size: int = 500) -> int:
    sql = "SELECT server_id FROM mcp_server_registry"
    all_servers = ws_query(sql)
    count = 0
    for i in range(0, len(all_servers), batch_size):
        batch = all_servers[i : i + batch_size]
        for row in batch:
            sid = row["server_id"]
            try:
                summary = compute_trust_summary(sid)
                if summary:
                    upsert_trust_summary(summary)
                    count += 1
            except Exception as e:
                log.warning(f"Failed to compute summary for {sid}: {e}")
        log.info(f"Processed {min(i + batch_size, len(all_servers))}/{len(all_servers)} servers")
    return count


def get_trust_summary_by_server_id(server_id: str) -> dict:
    sql = f"SELECT * FROM server_trust_summary WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return compute_trust_summary(server_id)


def get_top_risky_servers(limit: int = 20) -> list:
    sql = f"SELECT * FROM server_trust_summary WHERE verdict IN ('UNTRUSTED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT') ORDER BY trust_score ASC LIMIT {limit}"
    return ws_query(sql)


def get_verdict_distribution() -> dict:
    sql = "SELECT verdict, COUNT(*) as cnt FROM mcp_server_registry GROUP BY verdict ORDER BY cnt DESC"
    rows = ws_query(sql)
    return {row["verdict"]: row["cnt"] for row in rows}


def cycle() -> None:
    log.info("Starting trust summary computation cycle.")
    ensure_tables()
    total = refresh_all_summaries()
    log.info(f"Cycle complete. Processed {total} server summaries.")
    send_heartbeat("running", f"processed={total}")


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    log.info(f"{SERVICE_NAME} starting on port {PORT}.")

    try:
        ensure_tables()
    except Exception as e:
        log.error(f"Failed to initialize tables: {e}")

    send_heartbeat("running", "started")

    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Cycle error: {e}")
            send_heartbeat("error", str(e)[:100])

        time.sleep(300)


if __name__ == "__main__":
    run()