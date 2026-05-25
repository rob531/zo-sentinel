import logging
import os
import time
import signal
import json
import hashlib
from datetime import datetime, timezone

import requests

SERVICE_NAME = "diagnose_core_tables"
SERVICE_PORT = None  # no HTTP server needed
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_TIMEOUT = 15
WRITE_TIMEOUT = 15
POLL_SECS = 120

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")],
)


def ws_query(sql: str) -> list:
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> None:
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={"table": table, "rows": rows},
        timeout=WRITE_TIMEOUT,
    )
    resp.raise_for_status()


def send_heartbeat(status: str, meta: dict) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": status,
        "ts": ts,
        "meta": json.dumps(meta),
    }])


def check_single_instance() -> None:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Already running with PID %d, exiting", old_pid)
            raise SystemExit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file() -> None:
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    logger.info("Received signal %d, shutting down", signum)
    remove_pid_file()
    raise SystemExit(0)


def check_table_exists(table_name: str) -> dict:
    sql = f"""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = '{table_name}'
    ORDER BY ordinal_position
    """
    try:
        cols = ws_query(sql)
        return {"exists": True, "columns": [c["column_name"] for c in cols], "count": len(cols)}
    except Exception as e:
        logger.warning("Error checking table %s: %s", table_name, e)
        return {"exists": False, "columns": [], "count": 0, "error": str(e)}


def count_rows(table_name: str) -> dict:
    sql = f"SELECT COUNT(*) AS cnt FROM {table_name}"
    try:
        rows = ws_query(sql)
        return {"count": rows[0]["cnt"] if rows else 0}
    except Exception as e:
        logger.warning("Error counting rows in %s: %s", table_name, e)
        return {"count": -1, "error": str(e)}


def check_table_pk(table_name: str) -> dict:
    sql = f"""
    SELECT constraint_name, column_name
    FROM information_schema.key_column_usage
    WHERE table_name = '{table_name}'
      AND constraint_name LIKE '%pkey%'
    LIMIT 5
    """
    try:
        keys = ws_query(sql)
        return {"pks": [k["column_name"] for k in keys]}
    except Exception as e:
        logger.warning("Error checking PK for %s: %s", table_name, e)
        return {"pks": [], "error": str(e)}


def diagnose_core_tables() -> dict:
    targets = [
        "mcp_server_registry",
        "mcp_signal_scores",
        "mcp_fingerprints",
        "mcp_attestations",
        "mcp_registry_facts",
        "mcp_threat_associations",
        "service_health",
    ]
    results = {}
    for tbl in targets:
        schema = check_table_exists(tbl)
        if schema["exists"]:
            row_count = count_rows(tbl)
            pk_info = check_table_pk(tbl)
            results[tbl] = {
                "status": "PRESENT",
                "column_count": schema["count"],
                "columns": schema["columns"],
                "row_count": row_count["count"],
                "primary_keys": pk_info["pks"],
            }
        else:
            results[tbl] = {
                "status": "MISSING",
                "error": schema.get("error", "table not found in information_schema"),
                "row_count": None,
                "columns": [],
                "primary_keys": [],
            }
    return results


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Starting %s diagnostic daemon", SERVICE_NAME)

    while True:
        try:
            findings = diagnose_core_tables()
            logger.info("Core table findings: %s", json.dumps(findings, indent=2))
            send_heartbeat("ok", findings)
        except Exception as e:
            logger.error("Error during diagnosis: %s", e)
            send_heartbeat("error", {"error": str(e)})

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()