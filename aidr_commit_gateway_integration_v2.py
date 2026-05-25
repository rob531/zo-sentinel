import signal
import os
import sys
import time
import uuid
import requests
import uvicorn
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel
import json

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "aidr_commit_gateway.log"
PID_FILE = "/tmp/aidr_commit_gateway.pid"

SERVICE_NAME = "aidr_commit_gateway"
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
RISK_OVERRIDE_THRESHOLD = "CAUTION_LIMITED"

RISK_TIER_BLOCKED = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}

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
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error for table {table}: {e}")
        return False

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"ws_query error: {e}")
        return None

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}")
        return False

def send_heartbeat():
    ts = datetime.now(timezone.utc).isoformat()
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running"
    }]
    ws_write("service_health", rows)

def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)

def ensure_audit_log_table():
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        target_server_id VARCHAR,
        event_type VARCHAR,
        actor VARCHAR,
        detail VARCHAR,
        created_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)

def get_server_verdict(server_id: str) -> Optional[str]:
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0].get("verdict")
    return None

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    sql = f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0].get("score")
    return None

def is_verdict_blocked(verdict: Optional[str]) -> bool:
    if verdict is None:
        return False
    return verdict in RISK_TIER_BLOCKED

def check_commit_allowed(server_id: str, override: bool = False) -> Dict[str, Any]:
    verdict = get_server_verdict(server_id)
    
    if verdict is None:
        return {
            "allowed": True,
            "reason": "no_verdict",
            "verdict": None
        }
    
    if override:
        log(f"Override flag present for server {server_id}, verdict={verdict}")
        return {
            "allowed": True,
            "reason": "override_accepted",
            "verdict": verdict
        }
    
    if verdict == "KNOWN_THREAT":
        return {
            "allowed": False,
            "reason": "known_threat_blocked",
            "verdict": verdict
        }
    
    if verdict in RISK_TIER_BLOCKED:
        return {
            "allowed": False,
            "reason": f"verdict_blocked",
            "verdict": verdict
        }
    
    return {
        "allowed": True,
        "reason": "verdict_passed",
        "verdict": verdict
    }

def log_commit_attempt(
    server_id: str,
    verdict: Optional[str],
    allowed: bool,
    reason: str,
    actor: str = "aidr_gateway"
):
    ts = datetime.now(timezone.utc).isoformat()
    detail = json.dumps({
        "event": "commit_attempt",
        "server_id": server_id,
        "verdict": verdict,
        "allowed": allowed,
        "reason": reason,
        "timestamp": ts
    })
    rows = [{
        "target_server_id": server_id,
        "event_type": "commit_attempt",
        "actor": actor,
        "detail": detail,
        "created_at": ts
    }]
    ws_write("audit_log", rows)
    log(f"Commit attempt logged: server={server_id}, verdict={verdict}, allowed={allowed}, reason={reason}")

class CommitRequest(BaseModel):
    server_id: str
    commit_payload: Dict[str, Any]
    override: bool = False

class CommitResponse(BaseModel):
    success: bool
    message: str
    verdict: Optional[str]
    injection_resilience_score: Optional[float]
    commit_executed: bool

@app.post("/commit")
async def commit_to_mcp(request: CommitRequest, authorization: Optional[str] = Header(None)):
    server_id = request.server_id
    override = request.override
    
    commit_check = check_commit_allowed(server_id, override=override)
    
    verdict = commit_check.get("verdict")
    allowed = commit_check.get("allowed")
    reason = commit_check.get("reason")
    
    injection_resilience_score = get_injection_resilience_score(server_id)
    
    log_commit_attempt(
        server_id=server_id,
        verdict=verdict,
        allowed=allowed,
        reason=reason
    )
    
    if not allowed:
        return CommitResponse(
            success=False,
            message=f"Commit blocked: {reason}. Verdict={verdict}",
            verdict=verdict,
            injection_resilience_score=injection_resilience_score,
            commit_executed=False
        )
    
    payload = request.commit_payload.copy()
    if injection_resilience_score is not None:
        payload["injection_resilience_score"] = injection_resilience_score
        log(f"Injected injection_resilience_score={injection_resilience_score} into commit payload for {server_id}")
    
    try:
        if AIDR_API_TOKEN:
            aidr_resp = requests.post(
                f"{AIDR_API_BASE}/commit",
                json=payload,
                headers=AIDR_HEADERS,
                timeout=30
            )
            aidr_resp.raise_for_status()
            log(f"Commit executed via AIDR for server {server_id}")
        else:
            log(f"AIDR token not configured, simulating commit for {server_id}")
        
        return CommitResponse(
            success=True,
            message=f"Commit executed for {server_id}",
            verdict=verdict,
            injection_resilience_score=injection_resilience_score,
            commit_executed=True
        )
    except Exception as e:
        log(f"Commit failed for {server_id}: {e}")
        return CommitResponse(
            success=False,
            message=f"Commit failed: {str(e)}",
            verdict=verdict,
            injection_resilience_score=injection_resilience_score,
            commit_executed=False
        )

@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.get("/verdict/{server_id}")
async def get_verdict(server_id: str):
    verdict = get_server_verdict(server_id)
    commit_check = check_commit_allowed(server_id, override=False)
    injection_score = get_injection_resilience_score(server_id)
    
    return {
        "server_id": server_id,
        "verdict": verdict,
        "commit_allowed": commit_check.get("allowed"),
        "block_reason": commit_check.get("reason") if not commit_check.get("allowed") else None,
        "injection_resilience_score": injection_score
    }

@app.on_event("startup")
async def startup():
    check_single_instance()
    ensure_audit_log_table()
    log(f"{SERVICE_NAME} started on port {PORT}")

def run():
    import threading
    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    uvicorn.run(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    run()