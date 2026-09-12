import logging
import os
import signal
import sys
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = "perspective_summary_api"
SERVICE_PORT = 8796
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title=SERVICE_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_start_time = time.time()


def get_uptime_seconds() -> float:
    return time.time() - _start_time


def ws_query(sql: str) -> list:
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def send_heartbeat() -> None:
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "status": "running",
            "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "uptime_seconds": get_uptime_seconds()
        }])
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame) -> None:
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def compute_deterministic_id(*args) -> str:
    import hashlib
    content = "_".join(str(a) for a in args)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_perspective_summary(
    server_id: str = None,
    perspective_model: str = None,
    time_window_days: int = 30
) -> dict:
    """Fetch perspective summary data for the UI."""
    conditions = []
    params = {}

    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id

    if perspective_model:
        conditions.append("perspective_model = :perspective_model")
        params["perspective_model"] = perspective_model

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    sql = f"""
        SELECT
            server_id,
            perspective_model,
            perspective_name,
            score,
            confidence,
            evidence_count,
            key_signals,
            computed_at,
            metadata
        FROM mcp_perspective_summary
        WHERE {where_clause}
        ORDER BY computed_at DESC
        LIMIT 1000
    """

    rows = ws_query(sql)

    summary = {
        "total_records": len(rows),
        "records": rows,
        "score_distribution": {},
        "confidence_distribution": {},
        "perspective_models": [],
        "server_count": 0
    }

    if rows:
        summary["server_count"] = len(set(r.get("server_id") for r in rows))
        summary["perspective_models"] = list(set(r.get("perspective_model") for r in rows if r.get("perspective_model")))

        for row in rows:
            score = row.get("score", 0)
            score_bucket = f"{(score // 10) * 10}-{(score // 10) * 10 + 9}"
            summary["score_distribution"][score_bucket] = summary["score_distribution"].get(score_bucket, 0) + 1

            conf = row.get("confidence", 0)
            conf_bucket = "high" if conf >= 0.7 else ("medium" if conf >= 0.4 else "low")
            summary["confidence_distribution"][conf_bucket] = summary["confidence_distribution"].get(conf_bucket, 0) + 1

    return summary


def get_perspective_by_server(server_id: str) -> dict:
    """Get all perspective summaries for a specific server."""
    sql = f"""
        SELECT
            server_id,
            perspective_model,
            perspective_name,
            score,
            confidence,
            evidence_count,
            key_signals,
            computed_at,
            metadata
        FROM mcp_perspective_summary
        WHERE server_id = '{server_id}'
        ORDER BY perspective_model, computed_at DESC
    """

    rows = ws_query(sql)

    perspectives = {}
    for row in rows:
        model = row.get("perspective_model", "default")
        if model not in perspectives:
            perspectives[model] = {
                "perspective_model": model,
                "perspective_name": row.get("perspective_name"),
                "scores": [],
                "latest_score": None,
                "average_score": 0,
                "confidence": row.get("confidence", 0)
            }
        perspectives[model]["scores"].append({
            "score": row.get("score", 0),
            "computed_at": row.get("computed_at")
        })

    for model_data in perspectives.values():
        if model_data["scores"]:
            scores = [s["score"] for s in model_data["scores"]]
            model_data["latest_score"] = scores[0]
            model_data["average_score"] = sum(scores) / len(scores)

    return {
        "server_id": server_id,
        "perspective_count": len(perspectives),
        "perspectives": list(perspectives.values())
    }


def get_perspective_trends(
    perspective_model: str = None,
    time_window_days: int = 7
) -> dict:
    """Get trend data for perspective scores over time."""
    sql = f"""
        SELECT
            server_id,
            perspective_model,
            perspective_name,
            score,
            computed_at
        FROM mcp_perspective_summary
        WHERE computed_at >= NOW() - INTERVAL '{time_window_days} days'
    """

    if perspective_model:
        sql = f"""
            SELECT
                server_id,
                perspective_model,
                perspective_name,
                score,
                computed_at
            FROM mcp_perspective_summary
            WHERE perspective_model = '{perspective_model}'
            AND computed_at >= NOW() - INTERVAL '{time_window_days} days'
        """

    rows = ws_query(sql)

    trends = {}
    for row in rows:
        model = row.get("perspective_model", "unknown")
        date_key = str(row.get("computed_at", ""))[:10]

        if model not in trends:
            trends[model] = {}

        if date_key not in trends[model]:
            trends[model][date_key] = {"scores": [], "count": 0}

        trends[model][date_key]["scores"].append(row.get("score", 0))
        trends[model][date_key]["count"] += 1

    for model, dates in trends.items():
        for date_key, data in dates.items():
            if data["scores"]:
                data["average_score"] = sum(data["scores"]) / len(data["scores"])
                data["min_score"] = min(data["scores"])
                data["max_score"] = max(data["scores"])

    return {
        "time_window_days": time_window_days,
        "perspective_model": perspective_model,
        "trends": trends
    }


def get_perspective_comparison(server_ids: list) -> dict:
    """Compare perspectives across multiple servers."""
    if not server_ids:
        raise HTTPException(status_code=400, detail="server_ids required")

    server_id_list = "', '".join(server_ids)
    sql = f"""
        SELECT
            server_id,
            perspective_model,
            perspective_name,
            score,
            confidence,
            evidence_count,
            key_signals,
            computed_at
        FROM mcp_perspective_summary
        WHERE server_id IN ('{server_id_list}')
        AND computed_at >= NOW() - INTERVAL '30 days'
        ORDER BY server_id, perspective_model, computed_at DESC
    """

    rows = ws_query(sql)

    comparison = {sid: {} for sid in server_ids}
    for row in rows:
        sid = row.get("server_id")
        model = row.get("perspective_model", "default")
        if model not in comparison[sid]:
            comparison[sid][model] = {
                "latest_score": row.get("score", 0),
                "confidence": row.get("confidence", 0),
                "evidence_count": row.get("evidence_count", 0),
                "key_signals": row.get("key_signals", [])
            }

    return {
        "server_ids": server_ids,
        "perspectives": comparison,
        "metrics": {
            "models_compared": len(set(r.get("perspective_model") for r in rows)),
            "total_records": len(rows)
        }
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": get_uptime_seconds()
    }


@app.get("/api/v1/perspective/summary")
def perspective_summary(
    server_id: str = None,
    perspective_model: str = None,
    time_window_days: int = 30
):
    return get_perspective_summary(server_id, perspective_model, time_window_days)


@app.get("/api/v1/perspective/server/{server_id}")
def perspective_by_server(server_id: str):
    return get_perspective_by_server(server_id)


@app.get("/api/v1/perspective/trends")
def perspective_trends(
    perspective_model: str = None,
    time_window_days: int = 7
):
    return get_perspective_trends(perspective_model, time_window_days)


@app.post("/api/v1/perspective/compare")
def perspective_compare(server_ids: list):
    return get_perspective_comparison(server_ids)


@app.post("/api/v1/perspective/score")
def submit_perspective_score(data: dict):
    """Submit a new perspective score."""
    required_fields = ["server_id", "perspective_model", "score"]
    for field in required_fields:
        if field not in data:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

    server_id = data["server_id"]
    perspective_model = data["perspective_model"]
    perspective_name = data.get("perspective_name", perspective_model)
    score = data["score"]
    confidence = data.get("confidence", 0.5)
    evidence_count = data.get("evidence_count", 0)
    key_signals = data.get("key_signals", [])
    metadata = data.get("metadata", {})

    computed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    row_id = compute_deterministic_id(server_id, perspective_model, computed_at)

    success = ws_write("mcp_perspective_summary", [{
        "id": row_id,
        "server_id": server_id,
        "perspective_model": perspective_model,
        "perspective_name": perspective_name,
        "score": score,
        "confidence": confidence,
        "evidence_count": evidence_count,
        "key_signals": str(key_signals),
        "computed_at": computed_at,
        "metadata": str(metadata)
    }])

    if success:
        return {"status": "ok", "id": row_id}
    else:
        raise HTTPException(status_code=500, detail="Failed to write perspective score")


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
        time.sleep(60)


def run():
    check_single_instance()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)


if __name__ == "__main__":
    run()