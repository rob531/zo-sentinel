import signal
import os
import sys
import time
import hashlib
import requests
import uvicorn
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import json

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "aidr_verdict_check_integration.log"
PID_FILE = "/tmp/aidr_verdict_check_integration.pid"

SERVICE_NAME = "aidr_verdict_check_integration"
PORT = 3892
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
AIDR_GATEWAY_URL = "http://127.0.0.1:3891"
HEARTBEAT_INTERVAL = 30
PROHIBITED_VERDICTS = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"}
OVERRIDE_REQUIRED_VERDICTS = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()

class CommitRequest(BaseModel):
    server_id: str
    commit_sha: str
    commit_message: str
    author: str
    branch: str
    override: bool = False
    override_reason: Optional[str] = None
    injection_resilience_score: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    logger.info(msg)


def signal_handler(sig, frame):
    log(f"Received signal {sig}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Already running as PID {old_pid}, exiting")
            sys.exit(1)
        except OSError:
            log(f"Stale PID file found for {old_pid}, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log(f"Started with PID {os.getpid()}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log(f"ws_write failed for table {table}: {e}")
        return False


def send_heartbeat():
    ts = utc_now_iso()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "ok",
        "meta": json.dumps({"port": PORT})
    }
    ws_write("service_health", [row])


def get_server_verdict(server_id: str) -> Optional[str]:
    sql = "SELECT verdict FROM mcp_server_registry WHERE server_id = ?"
    rows = ws_query(sql, {"p1": server_id})
    if rows:
        return rows[0].get("verdict")
    return None


def get_injection_resilience_score(server_id: str) -> Optional[float]:
    sql = "SELECT score FROM mcp_signal_scores WHERE server_id = ? AND signal_name = 'injection_resilience'"
    rows = ws_query(sql, {"p1": server_id})
    if rows:
        try:
            return float(rows[0].get("score", 0))
        except (ValueError, TypeError):
            return None
    return None


def validate_commit_request(req: CommitRequest) -> Dict[str, Any]:
    verdict = get_server_verdict(req.server_id)
    if verdict is None:
        return {
            "allowed": False,
            "reason": f"Server {req.server_id} not found in registry",
            "verdict": None,
            "injection_resilience": None
        }

    injection_resilience = req.injection_resilience_score
    if injection_resilience is None:
        inj_score = get_injection_resilience_score(req.server_id)
        if inj_score is not None:
            injection_resilience = inj_score

    if verdict in PROHIBITED_VERDICTS:
        if req.override and req.override_reason:
            log(f"Override accepted for server {req.server_id} with verdict {verdict}: {req.override_reason}")
            return {
                "allowed": True,
                "reason": f"Override accepted for verdict {verdict}",
                "verdict": verdict,
                "injection_resilience": injection_resilience,
                "override_reason": req.override_reason,
                "override": True
            }
        else:
            return {
                "allowed": False,
                "reason": f"Verdict {verdict} requires explicit override for server {req.server_id}",
                "verdict": verdict,
                "injection_resilience": injection_resilience,
                "override_required": True
            }

    return {
        "allowed": True,
        "reason": f"Verdict {verdict} permits commit",
        "verdict": verdict,
        "injection_resilience": injection_resilience,
        "override": False
    }


def forward_to_aidr_gateway(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = requests.post(
            f"{AIDR_GATEWAY_URL}/commit",
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        if resp.status_code in (200, 201, 202):
            return {"success": True, "data": resp.json() if resp.text else {}}
        else:
            return {"success": False, "error": f"Gateway returned {resp.status_code}: {resp.text}"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


@app.post("/commit/validate")
def validate_commit(req: CommitRequest):
    validation = validate_commit_request(req)
    return validation


@app.post("/commit")
def submit_commit(req: CommitRequest):
    validation = validate_commit_request(req)
    if not validation["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Commit blocked by verdict policy",
                "validation": validation
            }
        )

    commit_payload = {
        "server_id": req.server_id,
        "commit_sha": req.commit_sha,
        "commit_message": req.commit_message,
        "author": req.author,
        "branch": req.branch,
        "verdict": validation["verdict"],
        "injection_resilience_score": validation.get("injection_resilience"),
        "override": validation.get("override", False),
        "override_reason": validation.get("override_reason"),
        "validated_at": utc_now_iso(),
        "metadata": req.metadata or {}
    }

    result = forward_to_aidr_gateway(commit_payload)
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Failed to forward commit to AIDR gateway",
                "gateway_error": result.get("error"),
                "validation": validation
            }
        )

    record_commit_audit(req, validation)
    return {
        "status": "committed",
        "commit_sha": req.commit_sha,
        "server_id": req.server_id,
        "validation": validation,
        "gateway_response": result.get("data", {})
    }


def record_commit_audit(req: CommitRequest, validation: Dict[str, Any]):
    audit_row = {
        "event_type": "verdict_check_commit",
        "target_server_id": req.server_id,
        "actor": req.author,
        "detail": json.dumps({
            "commit_sha": req.commit_sha,
            "verdict": validation.get("verdict"),
            "injection_resilience": validation.get("injection_resilience"),
            "override": validation.get("override", False),
            "override_reason": validation.get("override_reason"),
            "allowed": validation.get("allowed"),
            "branch": req.branch
        }),
        "created_at": utc_now_iso()
    }
    ws_write("audit_log", [audit_row])


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/verdict/{server_id}")
def get_verdict(server_id: str):
    verdict = get_server_verdict(server_id)
    if verdict is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    injection_resilience = get_injection_resilience_score(server_id)
    return {
        "server_id": server_id,
        "verdict": verdict,
        "injection_resilience": injection_resilience,
        "blocked": verdict in PROHIBITED_VERDICTS,
        "ts": utc_now_iso()
    }


@app.get("/injection-resilience/{server_id}")
def get_injection_resilience(server_id: str):
    score = get_injection_resilience_score(server_id)
    if score is None:
        raise HTTPException(status_code=404, detail=f"No injection_resilience score for {server_id}")
    return {"server_id": server_id, "score": score, "ts": utc_now_iso()}


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    import threading
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    log(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()