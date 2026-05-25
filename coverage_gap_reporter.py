import requests
import json
import time
import datetime
import threading
from typing import Any, Dict, List, Optional

SERVICE_NAME = "coverage_gap_reporter"
PORT = 8772
WRITE_SERVICE_URL = f"http://127.0.0.1:{PORT}"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
POLL_SECS = 30 * 60
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"


def log(msg: str) -> None:
    ts = datetime.datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def check_single_instance() -> bool:
    import os
    pid = os.getpid()
    try:
        with open(PID_FILE, "r") as f:
            existing = int(f.read().strip())
    except FileNotFoundError:
        existing = None
    except Exception:
        existing = None
    if existing and existing != pid:
        try:
            os.kill(existing, 0)
            log(f"Already running as PID {existing}, exiting")
            return False
        except OSError:
            log(f"Stale PID {existing}, will overwrite")
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        return True
    except Exception as e:
        log(f"Cannot write PID file: {e}")
        return False


def remove_pid_file() -> None:
    try:
        import os
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum: int, frame: Any) -> None:
    log(f"Received signal {signum}, shutting down")
    remove_pid_file()
    import sys
    sys.exit(0)


def _start_heartbeat_thread() -> threading.Thread:
    def heartbeat_loop() -> None:
        while True:
            try:
                send_heartbeat()
            except Exception as e:
                log(f"Heartbeat error: {e}")
            time.sleep(HEARTBEAT_INTERVAL)
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    return t


def send_heartbeat() -> None:
    now = datetime.datetime.utcnow().isoformat()
    payload = {
        "table": "service_health",
        "rows": {
            "service": SERVICE_NAME,
            "last_heartbeat": now
        }
    }
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        log(f"Heartbeat failed: {e}")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"Query error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"Write error: {e}")
        return False


def get_registered_total() -> int:
    result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if result:
        return int(result[0].get("cnt", 0))
    return 0


def get_evidenced_total() -> int:
    result = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_registry_facts")
    if result:
        return int(result[0].get("cnt", 0))
    return 0


def get_per_source_breakdown() -> Dict[str, int]:
    result = ws_query(
        "SELECT registry_source, COUNT(*) as cnt FROM mcp_server_registry GROUP BY registry_source"
    )
    breakdown = {}
    for row in result:
        src = row.get("registry_source") or "unknown"
        cnt = int(row.get("cnt", 0))
        breakdown[src] = cnt
    return breakdown


def get_signals_with_one_distinct_value() -> int:
    result = ws_query(
        "SELECT signal_name, COUNT(DISTINCT score) as distinct_vals "
        "FROM mcp_signal_scores GROUP BY signal_name HAVING COUNT(DISTINCT score) = 1"
    )
    return len(result)


def run_cycle() -> None:
    log("Starting coverage gap analysis cycle")
    registered_total = get_registered_total()
    evidenced_total = get_evidenced_total()
    evidence_coverage_pct = 0.0
    if registered_total > 0:
        evidence_coverage_pct = round((evidenced_total / registered_total) * 100, 2)
    per_source_breakdown = get_per_source_breakdown()
    signals_one_distinct = get_signals_with_one_distinct_value()
    generated_at = datetime.datetime.utcnow().isoformat()
    summary = {
        "registered_total": registered_total,
        "evidenced_total": evidenced_total,
        "evidence_coverage_pct": evidence_coverage_pct,
        "per_source_breakdown": per_source_breakdown,
        "signals_with_one_distinct_value": signals_one_distinct,
        "generated_at": generated_at
    }
    log(f"Coverage gap summary: registered={registered_total}, evidenced={evidenced_total}, "
        f"coverage={evidence_coverage_pct}%, one-distinct-signals={signals_one_distinct}")
    record = {
        "memory_type": "coverage_gap_summary",
        "importance": 0.85,
        "content": json.dumps(summary),
        "created_at": generated_at,
        "key": f"coverage_gap_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    }
    success = ws_write("mesh_memory", record)
    if success:
        log("Successfully wrote coverage_gap_summary to mesh_memory")
    else:
        log("Failed to write coverage_gap_summary to mesh_memory")


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log(f"Starting {SERVICE_NAME}")
    if not check_single_instance():
        return
    _start_heartbeat_thread()
    log(f"Running cycle every {POLL_SECS} seconds")
    while True:
        try:
            run_cycle()
        except Exception as e:
            log(f"Cycle error: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()