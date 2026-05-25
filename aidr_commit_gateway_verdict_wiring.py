import signal
import os
import sys
import time
import uuid
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "aidr_commit_gateway_verdict_wiring.log"
PID_FILE = "/tmp/aidr_commit_gateway_verdict_wiring.pid"

SERVICE_NAME = "aidr_commit_gateway_verdict_wiring"
PORT = 3891
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773/inference"
HEARTBEAT_INTERVAL = 30

AIDR_API_BASE = os.getenv("AIDR_API_BASE", "https://api.crowdstrike.com/ai-defense/v1")
AIDR_API_TOKEN = os.getenv("AIDR_API_TOKEN", "")
AIDR_HEADERS = {"Authorization": f"Bearer {AIDR_API_TOKEN}", "Content-Type": "application/json"}

PROHIBITED_VERDICTS = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"}

app = FastAPI()

def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

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
            log(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        elif isinstance(data, list):
            return data
        else:
            log(f"Unexpected query response: {data}")
            return None
    except Exception as e:
        log(f"ws_query error: {e}")
        return None

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}")
        return False

def send_heartbeat():
    now = utc_now_iso()
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": now,
        "status": "ok",
        "meta": f"verdict_wiring_active"
    }]
    ws_write("service_health", rows)

def get_server_verdict(server_id: str) -> Optional[str]:
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    result = ws_query(sql)
    if result and len(result) > 0:
        return result[0].get("verdict")
    return None

def has_exemption_override(server_id: str) -> bool:
    sql = f"SELECT 1 FROM mcp_exemptions WHERE server_id = '{server_id}' AND status = 'active' LIMIT 1"
    result = ws_query(sql)
    return result is not None and len(result) > 0

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    sql = f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'"
    result = ws_query(sql)
    if result and len(result) > 0:
        return float(result[0].get("score", 0))
    return None

def log_commit_attempt(
    server_id: str,
    verdict: Optional[str],
    action: str,
    allowed: bool,
    reason: str,
    injection_score: Optional[float] = None
):
    now = utc_now_iso()
    verdict_context = {
        "verdict": verdict,
        "injection_resilience_score": injection_score,
        "allowed": allowed,
        "reason": reason
    }
    rows = [{
        "event_type": "commit_gate_verdict_check",
        "target_server_id": server_id,
        "actor": "aidr_commit_gateway",
        "detail": json.dumps(verdict_context),
        "created_at": now
    }]
    ws_write("audit_log", rows)

def check_commit_allowed(server_id: str) -> tuple[bool, str, Optional[float]]:
    verdict = get_server_verdict(server_id)
    injection_score = get_injection_resilience_score(server_id)

    if verdict is None:
        return False, f"server_id={server_id} not found in registry", injection_score

    if verdict in PROHIBITED_VERDICTS:
        if has_exemption_override(server_id):
            log(f"Commit allowed for server_id={server_id} (verdict={verdict}, has exemption override)")
            return True, f"exemption_override_approved", injection_score
        else:
            log(f"Commit BLOCKED for server_id={server_id} (verdict={verdict}, no exemption)")
            return False, f"prohibited_verdict={verdict}", injection_score

    log(f"Commit allowed for server_id={server_id} (verdict={verdict})")
    return True, f"approved_verdict={verdict}", injection_score

def forward_commit_to_aidr(server_id: str, commit_data: Dict[str, Any]) -> tuple[bool, str]:
    if not AIDR_API_TOKEN:
        return False, "AIDR_API_TOKEN not configured"

    payload = {
        "server_id": server_id,
        "commit_data": commit_data,
        "timestamp": utc_now_iso()
    }

    try:
        resp = requests.post(
            f"{AIDR_API_BASE}/commit",
            json=payload,
            headers=AIDR_HEADERS,
            timeout=30
        )
        if resp.status_code in (200, 201, 202):
            return True, "commit_forwarded"
        else:
            return False, f"aidr_rejected_status={resp.status_code}"
    except Exception as e:
        return False, f"aidr_forward_error={str(e)}"

class CommitRequest(BaseModel):
    server_id: str
    commit_data: Dict[str, Any]
    skip_verdict_check: bool = False

@app.post("/commit")
async def commit_to_aidr(request: CommitRequest, x_aidr_token: Optional[str] = Header(None)):
    server_id = request.server_id
    commit_data = request.commit_data

    if not request.skip_verdict_check:
        allowed, reason, injection_score = check_commit_allowed(server_id)

        log_commit_attempt(
            server_id=server_id,
            verdict=get_server_verdict(server_id),
            action="commit_attempt",
            allowed=allowed,
            reason=reason,
            injection_score=injection_score
        )

        if not allowed:
            return {
                "status": "blocked",
                "server_id": server_id,
                "reason": reason,
                "verdict": get_server_verdict(server_id)
            }

        if injection_score is not None:
            commit_data["injection_resilience_score"] = injection_score

    success, msg = forward_commit_to_aidr(server_id, commit_data)
    if success:
        return {"status": "forwarded", "server_id": server_id, "message": msg}
    else:
        return {"status": "failed", "server_id": server_id, "message": msg}

@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}

@app.get("/check/{server_id}")
async def check_server(server_id: str):
    allowed, reason, injection_score = check_commit_allowed(server_id)
    verdict = get_server_verdict(server_id)
    return {
        "server_id": server_id,
        "verdict": verdict,
        "injection_resilience_score": injection_score,
        "commit_allowed": allowed,
        "reason": reason
    }

def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)

import json

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log(f"Starting {SERVICE_NAME} on port {PORT}")
    import threading
    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    run()