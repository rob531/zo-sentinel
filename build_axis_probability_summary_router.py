import logging
import os
import signal
import sys
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOGS_DIR = PROJECT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOGS_DIR / "axis_probability_summary_router.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger("axis_probability_summary_router")

SERVICE_NAME = "axis_probability_summary_router"
PORT = 8796
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"

app = FastAPI()


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_URL


def get_execute_url():
    return EXECUTE_URL


def ws_query(sql: str) -> dict:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows: list) -> dict:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return {"ok": False}


def ws_execute(sql: str) -> dict:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return {"ok": False}


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance is running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            logger.warning(f"Stale PID file found, removing")
            pid_file.unlink()


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    try:
        ws_write(
            "service_health",
            [
                {
                    "service": SERVICE_NAME,
                    "status": "running",
                    "ts": utc_now_iso(),
                    "meta": '{"version": "1.0.0"}',
                }
            ],
        )
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


def compute_contract_id(axis_name: str, computed_at: str) -> str:
    import hashlib

    content = f"{axis_name}:{computed_at}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_axis_probability_summary(limit: int = 100) -> dict:
    sql = f"""
    SELECT 
        signal_name,
        COUNT(*) as server_count,
        AVG(score) as avg_score,
        MIN(score) as min_score,
        MAX(score) as max_score,
        STDDEV(score) as stddev_score,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY score) as median_score,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY score) as q1_score,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY score) as q3_score
    FROM mcp_signal_scores
    WHERE signal_name IS NOT NULL
    GROUP BY signal_name
    ORDER BY signal_name
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result.get("rows", [])


def get_signal_distribution(signal_name: str) -> dict:
    sql = f"""
    SELECT 
        CASE 
            WHEN score >= 0.9 THEN 'very_high'
            WHEN score >= 0.7 THEN 'high'
            WHEN score >= 0.5 THEN 'medium'
            WHEN score >= 0.3 THEN 'low'
            ELSE 'very_low'
        END as score_bucket,
        COUNT(*) as count
    FROM mcp_signal_scores
    WHERE signal_name = '{signal_name}'
    GROUP BY score_bucket
    ORDER BY score_bucket
    """
    result = ws_query(sql)
    return result.get("rows", [])


@app.get("/health")
def health():
    uptime = 0
    try:
        pid_file = Path(PID_FILE)
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            uptime = int(time.time() - os.path.getctime(pid_file))
    except Exception:
        pass
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}


@app.get("/api/v1/axis/probability/summary")
def get_axis_probability_summary_api(limit: int = 100):
    rows = get_axis_probability_summary(limit)
    return {"ok": True, "data": rows, "count": len(rows)}


@app.get("/api/v1/axis/probability/distribution/{signal_name}")
def get_signal_distribution_api(signal_name: str):
    rows = get_signal_distribution(signal_name)
    return {"ok": True, "signal_name": signal_name, "data": rows, "count": len(rows)}


@app.get("/api/v1/axis/probability/stats")
def get_axis_stats_api():
    summary_rows = get_axis_probability_summary(100)
    total_servers = ws_query(
        "SELECT COUNT(DISTINCT server_id) as total FROM mcp_signal_scores"
    )
    total = total_servers.get("rows", [{}])[0].get("total", 0)

    total_signals = ws_query(
        "SELECT COUNT(DISTINCT signal_name) as total FROM mcp_signal_scores"
    )
    signal_count = total_signals.get("rows", [{}])[0].get("total", 0)

    return {
        "ok": True,
        "data": {
            "total_servers": total,
            "total_signals": signal_count,
            "axis_summary": summary_rows,
        },
    }


@app.post("/api/v1/axis/probability/contract")
def create_axis_contract_api(axis_name: str, description: str = ""):
    computed_at = utc_now_iso()
    contract_id = compute_contract_id(axis_name, computed_at)
    sql = f"""
    INSERT INTO axis_probability_contracts (
        contract_id, axis_name, description, computed_at, status
    ) VALUES (
        '{contract_id}', '{axis_name}', '{description}', '{computed_at}', 'active'
    )
    """
    result = ws_execute(sql)
    return {
        "ok": result.get("ok", False),
        "contract_id": contract_id,
        "axis_name": axis_name,
        "computed_at": computed_at,
    }


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
        time.sleep(60)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    logger.info(f"Starting {SERVICE_NAME} on port {PORT}")

    import threading

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()