import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/signal_discrimination_monitor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "signal_discrimination_monitor"
SERVICE_PORT = 8774
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

POLL_SECS = 3600
MIN_DISTINCT_SCORES = 20
GRACE_DAYS = 7

WEAK_SIGNALS = [
    "permission_scope",
    "temporal_stability",
    "tool_description_safety",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance already running with PID %d. Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            logger.warning("Stale PID file found for PID %d. Removing.", old_pid)
            os.remove(pid_file)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame: Any) -> None:
    logger.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: Dict, wait: bool = True) -> Dict:
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> List[Dict]:
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "rows" in result:
        return result["rows"]
    return result


def ws_execute(sql: str) -> Dict:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status: str = "running", meta: Optional[Dict] = None) -> None:
    rows = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {}
    }
    ws_write("service_health", rows)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ensure_monitor_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS signal_discrimination_audit (
        id INTEGER PRIMARY KEY,
        signal_type TEXT NOT NULL,
        distinct_score_count INTEGER NOT NULL,
        score_min DOUBLE,
        score_max DOUBLE,
        server_count INTEGER,
        evaluated_at TEXT NOT NULL,
        breach_level TEXT
    )
    """
    ws_execute(create_sql)


def get_signal_stats(signal_type: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT
        COUNT(DISTINCT score) AS distinct_score_count,
        MIN(score) AS score_min,
        MAX(score) AS score_max,
        COUNT(*) AS total_count
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    """
    try:
        rows = ws_query(sql)
        if rows and len(rows) > 0:
            return dict(rows[0])
    except Exception as e:
        logger.warning("Failed to query stats for signal '%s': %s", signal_type, e)
    return None


def get_signal_first_seen(signal_type: str) -> Optional[str]:
    sql = f"""
    SELECT MIN(computed_at) AS first_computed
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    """
    try:
        rows = ws_query(sql)
        if rows and len(rows) > 0:
            val = rows[0].get("first_computed") or rows[0].get("first_computed")
            return val
    except Exception as e:
        logger.warning("Failed to query first_seen for '%s': %s", signal_type, e)
    return None


def get_distinct_scores_for_signal(signal_type: str) -> List[Any]:
    sql = f"""
    SELECT DISTINCT score
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    ORDER BY score
    """
    try:
        rows = ws_query(sql)
        return [r.get("score") or r.get("score") for r in rows if r.get("score") is not None or "score" in r]
    except Exception as e:
        logger.warning("Failed to get distinct scores for '%s': %s", signal_type, e)
        return []


def compute_days_since_first(first_computed: Optional[str]) -> int:
    if not first_computed:
        return 0
    try:
        if "Z" in first_computed or "+" in first_computed or first_computed.endswith("Z"):
            dt = datetime.fromisoformat(first_computed.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(first_computed).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except Exception:
        return 0


def determine_breach_level(distinct_count: int, days_active: int) -> str:
    if distinct_count >= MIN_DISTINCT_SCORES:
        return "none"
    if days_active >= GRACE_DAYS:
        return "critical"
    if distinct_count == 0:
        return "no_data"
    return "warning"


def audit_signal(signal_type: str) -> Dict[str, Any]:
    stats = get_signal_stats(signal_type)
    first_seen = get_signal_first_seen(signal_type)
    days_active = compute_days_since_first(first_seen)
    distinct_scores = get_distinct_scores_for_signal(signal_type)
    distinct_count = len(distinct_scores)

    breach_level = determine_breach_level(distinct_count, days_active)

    score_min = None
    score_max = None
    server_count = 0

    if stats:
        score_min = stats.get("score_min")
        score_max = stats.get("score_max")
        server_count = stats.get("total_count", 0)

    record = {
        "id": None,
        "signal_type": signal_type,
        "distinct_score_count": distinct_count,
        "score_min": score_min,
        "score_max": score_max,
        "server_count": server_count,
        "evaluated_at": utc_now_iso(),
        "breach_level": breach_level
    }

    if breach_level in ("critical", "warning", "no_data"):
        logger.warning(
            "Signal '%s' breach: distinct=%d (threshold=%d), days_active=%d, breach=%s, server_count=%d",
            signal_type, distinct_count, MIN_DISTINCT_SCORES, days_active, breach_level, server_count
        )
    else:
        logger.info(
            "Signal '%s' OK: distinct=%d, days_active=%d, server_count=%d",
            signal_type, distinct_count, days_active, server_count
        )

    return record


def write_audit_record(record: Dict[str, Any]) -> None:
    rows = {
        "id": record.get("id"),
        "signal_type": record["signal_type"],
        "distinct_score_count": record["distinct_score_count"],
        "score_min": record["score_min"],
        "score_max": record["score_max"],
        "server_count": record["server_count"],
        "evaluated_at": record["evaluated_at"],
        "breach_level": record["breach_level"]
    }
    try:
        ws_write("signal_discrimination_audit", rows)
    except Exception as e:
        logger.error("Failed to write audit record for '%s': %s", record["signal_type"], e)


def get_all_signal_names_from_table() -> List[str]:
    sql = "SELECT DISTINCT signal_type FROM mcp_signal_enrichments"
    try:
        rows = ws_query(sql)
        return [r.get("signal_type") for r in rows if r.get("signal_type")]
    except Exception as e:
        logger.warning("Failed to list signal types from table: %s", e)
        return []


def cycle() -> Dict[str, Any]:
    logger.info("Starting discrimination audit cycle.")
    ensure_monitor_table()

    all_signals = get_all_signal_names_from_table()
    weak_map = {s: True for s in WEAK_SIGNALS}

    signals_to_audit = [s for s in all_signals if weak_map.get(s)]

    if not signals_to_audit:
        logger.info("No weak signals found in mcp_signal_enrichments. Auditing configured weak signals anyway.")
        signals_to_audit = WEAK_SIGNALS

    results = {}
    critical_breaches = []

    for sig in signals_to_audit:
        record = audit_signal(sig)
        write_audit_record(record)
        results[sig] = record
        if record["breach_level"] == "critical":
            critical_breaches.append(sig)

    overall_status = "degraded" if critical_breaches else "ok"
    meta = {
        "signals_audited": len(signals_to_audit),
        "critical_breaches": critical_breaches,
        "results": {
            sig: {
                "distinct": r["distinct_score_count"],
                "breach": r["breach_level"],
                "servers": r["server_count"]
            }
            for sig, r in results.items()
        }
    }

    send_heartbeat(status=overall_status, meta=meta)

    logger.info(
        "Audit cycle complete. Signals: %d, Critical breaches: %d",
        len(signals_to_audit), len(critical_breaches)
    )

    return {"status": overall_status, "meta": meta}


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("%s starting. Poll interval: %ds. Min distinct: %d. Grace days: %d",
                SERVICE_NAME, POLL_SECS, MIN_DISTINCT_SCORES, GRACE_DAYS)

    cycle()

    while True:
        time.sleep(POLL_SECS)
        try:
            cycle()
        except Exception as e:
            logger.exception("Error in cycle: %s", e)
            send_heartbeat(status="error", meta={"error": str(e)})


if __name__ == "__main__":
    run()