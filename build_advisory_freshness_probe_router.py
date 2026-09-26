import os
import sys
import time
import logging
import signal
from pathlib import Path
from datetime import datetime, timezone

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "advisory_freshness_probe_router.log")],
)
log = logging.getLogger(__name__)

SERVICE_NAME = "advisory_freshness_probe_router"
PORT = 8796
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
QUERY_ENDPOINT = f"{QUERY_SERVICE_URL}/query"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"
EXECUTE_ENDPOINT = f"{EXECUTE_SERVICE_URL}/execute"

STALE_THRESHOLD_HOURS = 24
POLL_SECS = 300


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_ENDPOINT,
            json={"sql": sql},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_ENDPOINT,
            json={"table": table, "rows": rows, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table=%s", e, table)
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error("Already running as PID %s. Exiting.", old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            log.warning("Stale PID file %s, removing.", PID_FILE)
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame) -> None:
    log.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: dict = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {},
    }
    ws_write("service_health", [row])


def get_advisory_tables() -> list:
    tables = ws_query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    )
    advisory_tables = []
    for t in tables:
        name = t.get("table_name", "")
        if any(kw in name.lower() for kw in ["threat", "advisory", "intel", "cve", "vuln"]):
            advisory_tables.append(name)
    return advisory_tables


def get_table_freshness(table: str) -> dict:
    ts_fields = ["last_scanned", "last_seen", "last_assessed", "computed_at", "reported_at", "attested_at", "scored_at", "created_at", "updated_at"]
    for field in ts_fields:
        result = ws_query(f"SELECT MAX({field}) as latest FROM {table} WHERE {field} IS NOT NULL LIMIT 1")
        if result:
            latest = result[0].get("latest")
            if latest:
                return {"table": table, "ts_field": field, "latest_ts": latest}
    result = ws_query(f"SELECT MAX(rowid) as cnt FROM {table}")
    if result:
        cnt = result[0].get("cnt", 0)
        return {"table": table, "ts_field": "rowid", "latest_ts": None, "row_count": cnt}
    return {"table": table, "ts_field": None, "latest_ts": None}


def check_advisory_freshness() -> list:
    tables = get_advisory_tables()
    results = []
    for table in tables:
        freshness = get_table_freshness(table)
        results.append(freshness)
    return results


def compute_freshness_status(freshness: dict) -> str:
    latest = freshness.get("latest_ts")
    if not latest:
        return "unknown"
    try:
        if isinstance(latest, str):
            dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        else:
            return "unknown"
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age_hours < 1:
            return "fresh"
        elif age_hours < STALE_THRESHOLD_HOURS:
            return "ok"
        else:
            return "stale"
    except Exception:
        return "unknown"


app = FastAPI()


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()})


@app.get("/probe")
def probe():
    results = check_advisory_freshness()
    summary = {}
    for r in results:
        status = compute_freshness_status(r)
        summary[r["table"]] = status
    stale_count = sum(1 for s in summary.values() if s == "stale")
    overall = "stale" if stale_count > 0 else "ok"
    return JSONResponse({
        "overall": overall,
        "stale_count": stale_count,
        "total_tables": len(results),
        "table_status": summary,
        "details": results,
        "ts": utc_now_iso(),
    })


@app.get("/probe/{table_name}")
def probe_table(table_name: str):
    freshness = get_table_freshness(table_name)
    status = compute_freshness_status(freshness)
    return JSONResponse({
        "table": table_name,
        "status": status,
        "freshness": freshness,
        "ts": utc_now_iso(),
    })


def cycle() -> None:
    results = check_advisory_freshness()
    stale_tables = []
    for r in results:
        status = compute_freshness_status(r)
        if status == "stale":
            stale_tables.append(r["table"])
    send_heartbeat(
        status="ok",
        meta={
            "tables_checked": len(results),
            "stale_tables": stale_tables,
            "threshold_hours": STALE_THRESHOLD_HOURS,
        },
    )


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("Starting %s on port %d", SERVICE_NAME, PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()
    while True:
        time.sleep(POLL_SECS)
        try:
            cycle()
        except Exception as e:
            log.error("cycle error: %s", e)
            send_heartbeat(status="error", meta={"error": str(e)})