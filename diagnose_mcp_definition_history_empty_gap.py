import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = "diagnose_mcp_definition_history_empty_gap"
SERVICE_PORT = 0
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL + "/query", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    payload = {"table": table, "rows": rows}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL + "/execute", json=payload, timeout=30)
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance is running with PID %d. Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            logger.warning("Stale PID file found (PID %d). Removing.", old_pid)
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame: Any) -> None:
    logger.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {},
    }
    ws_write("service_health", [row])


def table_exists(table_name: str) -> bool:
    rows = ws_query(
        f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}' LIMIT 1"
    )
    return len(rows) > 0


def get_table_row_count(table_name: str) -> int:
    rows = ws_query(f"SELECT COUNT(*) AS cnt FROM {table_name}")
    if rows:
        return rows[0].get("cnt", 0) or 0
    return 0


def get_table_columns(table_name: str) -> List[str]:
    rows = ws_query(
        f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position"
    )
    return [r.get("column_name", "") for r in rows if r.get("column_name")]


def get_recent_ingestion_records(table_name: str, days: int = 7) -> List[Dict[str, Any]]:
    ts_col = None
    columns = get_table_columns(table_name)
    for col in columns:
        if "timestamp" in col.lower() or "created" in col.lower() or "added" in col.lower():
            ts_col = col
            break
    if ts_col is None:
        return []
    sql = f"SELECT * FROM {table_name} WHERE {ts_col} >= NOW() - INTERVAL '{days} days' ORDER BY {ts_col} DESC LIMIT 10"
    return ws_query(sql)


def get_signal_analyser_heartbeat() -> Optional[Dict[str, Any]]:
    rows = ws_query(
        "SELECT last_heartbeat, status, meta FROM service_health WHERE service = 'signal_analyser' ORDER BY last_heartbeat DESC LIMIT 1"
    )
    return rows[0] if rows else None


def get_scanner_heartbeat() -> Optional[Dict[str, Any]]:
    rows = ws_query(
        "SELECT last_heartbeat, status, meta FROM service_health WHERE service = 'mcp_scanner' ORDER BY last_heartbeat DESC LIMIT 1"
    )
    return rows[0] if rows else None


def get_registry_recent_additions(days: int = 7) -> List[Dict[str, Any]]:
    rows = ws_query(
        f"SELECT server_id, name, first_seen FROM mcp_server_registry WHERE first_seen >= NOW() - INTERVAL '{days} days' ORDER BY first_seen DESC LIMIT 20"
    )
    return rows


def get_definition_history_recent(days: int = 7) -> List[Dict[str, Any]]:
    if not table_exists("mcp_definition_history"):
        return []
    ts_col = None
    columns = get_table_columns("mcp_definition_history")
    for col in columns:
        if "timestamp" in col.lower() or "recorded" in col.lower() or "created" in col.lower():
            ts_col = col
            break
    if ts_col is None:
        return ws_query(f"SELECT * FROM mcp_definition_history ORDER BY 1 DESC LIMIT 10")
    return ws_query(
        f"SELECT * FROM mcp_definition_history WHERE {ts_col} >= NOW() - INTERVAL '{days} days' ORDER BY {ts_col} DESC LIMIT 10"
    )


def get_audit_events_for_definition(days: int = 7) -> List[Dict[str, Any]]:
    if not table_exists("audit_log"):
        return []
    return ws_query(
        f"SELECT event_id, event_type, action, outcome, timestamp FROM audit_log WHERE event_type LIKE '%definition%' OR action LIKE '%definition%' ORDER BY timestamp DESC LIMIT 20"
    )


def compute_heartbeat_age_minutes(heartbeat: Optional[Dict[str, Any]]) -> Optional[float]:
    if not heartbeat or not heartbeat.get("last_heartbeat"):
        return None
    try:
        hb_ts = datetime.fromisoformat(heartbeat["last_heartbeat"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - hb_ts.replace(tzinfo=timezone.utc)).total_seconds() / 60.0
    except (ValueError, AttributeError):
        return None


def diagnose() -> Dict[str, Any]:
    diagnosis: Dict[str, Any] = {
        "run_at": utc_now_iso(),
        "tables_checked": {},
        "services_checked": {},
        "gaps_found": [],
        "recommendations": [],
        "summary": "ok",
    }

    definition_table_exists = table_exists("mcp_definition_history")
    diagnosis["tables_checked"]["mcp_definition_history_exists"] = definition_table_exists

    if not definition_table_exists:
        diagnosis["summary"] = "table_missing"
        diagnosis["gaps_found"].append("mcp_definition_history table does not exist in the database")
        diagnosis["recommendations"].append(
            "Run schema bootstrap to create mcp_definition_history table"
        )
        return diagnosis

    definition_count = get_table_row_count("mcp_definition_history")
    diagnosis["tables_checked"]["mcp_definition_history_row_count"] = definition_count

    registry_count = get_table_row_count("mcp_server_registry")
    diagnosis["tables_checked"]["mcp_server_registry_row_count"] = registry_count

    recent_definitions = get_definition_history_recent(days=7)
    diagnosis["tables_checked"]["recent_definition_history_count"] = len(recent_definitions)

    recent_registry = get_registry_recent_additions(days=7)
    diagnosis["tables_checked"]["recent_registry_additions"] = len(recent_registry)

    sa_hb = get_signal_analyser_heartbeat()
    if sa_hb:
        sa_age = compute_heartbeat_age_minutes(sa_hb)
        diagnosis["services_checked"]["signal_analyser"] = {
            "last_heartbeat": sa_hb.get("last_heartbeat"),
            "status": sa_hb.get("status"),
            "age_minutes": round(sa_age, 1) if sa_age else None,
        }
        if sa_age and sa_age > 60:
            diagnosis["gaps_found"].append(
                f"signal_analyser heartbeat is stale ({round(sa_age, 1)} minutes old)"
            )
            diagnosis["recommendations"].append(
                "Check signal_analyser daemon health; it may be stuck or not running"
            )
    else:
        diagnosis["services_checked"]["signal_analyser"] = {
            "last_heartbeat": None,
            "status": "not_found",
        }
        diagnosis["gaps_found"].append("signal_analyser has no heartbeat record in service_health")
        diagnosis["recommendations"].append(
            "Ensure signal_analyser daemon is registered and sending heartbeats"
        )

    sc_hb = get_scanner_heartbeat()
    if sc_hb:
        sc_age = compute_heartbeat_age_minutes(sc_hb)
        diagnosis["services_checked"]["mcp_scanner"] = {
            "last_heartbeat": sc_hb.get("last_heartbeat"),
            "status": sc_hb.get("status"),
            "age_minutes": round(sc_age, 1) if sc_age else None,
        }
        if sc_age and sc_age > 60:
            diagnosis["gaps_found"].append(
                f"mcp_scanner heartbeat is stale ({round(sc_age, 1)} minutes old)"
            )
    else:
        diagnosis["services_checked"]["mcp_scanner"] = {
            "last_heartbeat": None,
            "status": "not_found",
        }

    audit_events = get_audit_events_for_definition(days=7)
    diagnosis["tables_checked"]["recent_audit_definition_events"] = len(audit_events)

    if definition_count == 0 and registry_count > 0:
        diagnosis["summary"] = "empty_with_data"
        diagnosis["gaps_found"].append(
            f"mcp_definition_history is empty but mcp_server_registry has {registry_count} rows"
        )
        diagnosis["recommendations"].extend([
            "Verify signal_analyser daemon is actively processing servers",
            "Check that signal_analyser writes to mcp_definition_history (not just mcp_signal_scores)",
            "Review signal_analyser logs for processing errors or rate-limiting",
        ])

    if definition_count > 0 and len(recent_definitions) == 0:
        diagnosis["summary"] = "stale"
        diagnosis["gaps_found"].append(
            "mcp_definition_history has historical data but no new records in the past 7 days"
        )
        diagnosis["recommendations"].append(
            "Ingestion pipeline may have stalled; check signal_analyser and scanner daemons"
        )

    if registry_count == 0:
        diagnosis["summary"] = "no_source_data"
        diagnosis["gaps_found"].append("mcp_server_registry is empty — no servers have been registered")
        diagnosis["recommendations"].append(
            "Run registry ingestor to populate mcp_server_registry before definition history can be built"
        )

    if diagnosis["summary"] == "ok" and definition_count > 0:
        diagnosis["recommendations"].append("Definition history ingestion is healthy")

    return diagnosis


def write_diagnostic_result(diagnosis: Dict[str, Any]) -> None:
    result_row = {
        "diagnostic": SERVICE_NAME,
        "run_at": diagnosis["run_at"],
        "summary": diagnosis["summary"],
        "tables_checked": str(diagnosis["tables_checked"]),
        "services_checked": str(diagnosis["services_checked"]),
        "gaps_found": len(diagnosis["gaps_found"]),
        "recommendations": "; ".join(diagnosis["recommendations"])[:2000],
    }
    ws_write("service_health", [result_row])


def cycle() -> None:
    logger.info("Running mcp_definition_history gap diagnostic cycle")
    try:
        diagnosis = diagnose()
        logger.info(
            "Diagnostic result: summary=%s gaps_found=%d",
            diagnosis["summary"],
            len(diagnosis["gaps_found"]),
        )
        for gap in diagnosis["gaps_found"]:
            logger.warning("  GAP: %s", gap)
        for rec in diagnosis["recommendations"]:
            logger.info("  REC: %s", rec)
        write_diagnostic_result(diagnosis)
    except requests.exceptions.ConnectionError as e:
        logger.error("WriteService unreachable: %s", e)
    except Exception as e:
        logger.exception("Diagnostic cycle failed: %s", e)


def run() -> None:
    import signal

    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("%s daemon starting", SERVICE_NAME)

    POLL_SECS = 300

    while True:
        cycle()
        send_heartbeat(status="running", meta={"last_cycle": utc_now_iso()})
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()