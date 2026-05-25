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
            log(f"Stale PID file for {old_pid}, removing")
            remove_pid_file()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        r = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"ws_query error: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        rv = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=15)
        rv.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        rv = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=15)
        rv.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}")
        return False

def send_heartbeat():
    try:
        requests.post(WRITE_SERVICE_URL, json={"table": "service_health", "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()}],"wait":True}, timeout=5)
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def ensure_tables():
    ws_execute("CREATE TABLE IF NOT EXISTS mcp_aidr_commit_log (commit_id VARCHAR, server_id VARCHAR, verdict VARCHAR, injection_resilience_score DOUBLE, commit_status VARCHAR, override_used BOOLEAN, actor VARCHAR, detail VARCHAR, created_at TIMESTAMP)")

def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = ? LIMIT 1"
    rows = ws_query(sql, {"p1": server_id})
    if rows:
        return rows[0]
    return None

def get_signal_score(server_id: str, signal_name: str = "injection_resilience") -> Optional[float]:
    sql = "SELECT score FROM mcp_signal_scores WHERE server_id = ? AND signal_name = ? ORDER BY scored_at DESC LIMIT 1"
    rows = ws_query(sql, {"p1": server_id, "p2": signal_name})
    if rows:
        return float(rows[0].get("score", 0))
    return None

def check_inference_router(server_id: str) -> Dict[str, Any]:
    try:
        rv = requests.post(f"{INFERENCE_ROUTER_URL}", json={"server_id": server_id, "check": "verdict"}, timeout=20)
        if rv.ok:
            return rv.json()
    except Exception as e:
        log(f"inference_router check failed: {e}, falling back to direct DB query")
    verdict_data = get_server_verdict(server_id)
    if verdict_data:
        return {"verdict": verdict_data.get("verdict"), "trust_score": verdict_data.get("trust_score"), "source": "db"}
    return {"verdict": "UNKNOWN", "trust_score": 0.0, "source": "db"}

def forward_to_aidr(server_id: str, commit_payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
    if not AIDR_API_TOKEN:
        return {"ok": False, "error": "AIDR_API_TOKEN not configured", "commit_id": None}
    try:
        rv = requests.post(f"{AIDR_API_BASE}/runtime/commit", json=commit_payload, headers=AIDR_HEADERS, timeout=30)
        if rv.ok:
            resp = rv.json()
            return {"ok": True, "commit_id": resp.get("commit_id"), "status": resp.get("status")}
        else:
            return {"ok": False, "error": f"AIDR API error {rv.status_code}: {rv.text}", "commit_id": None}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e), "commit_id": None}

def log_commit_decision(commit_id: str, server_id: str, verdict: str, injection_score: float, status: str, override_used: bool, actor: str, detail: str):
    ws_write("mcp_aidr_commit_log", [{"commit_id": commit_id, "server_id": server_id, "verdict": verdict, "injection_resilience_score": injection_score, "commit_status": status, "override_used": override_used, "actor": actor, "detail": detail, "created_at": datetime.now(timezone.utc).isoformat()}])
    ws_write("audit_log", [{"target_server_id": server_id, "event_type": "AIDR_COMMIT_DECISION", "actor": actor, "detail": json.dumps({"commit_id": commit_id, "verdict": verdict, "injection_resilience_score": injection_score, "status": status, "override_used": override_used, "detail": detail}), "created_at": datetime.now(timezone.utc).isoformat()}])

class CommitRequest(BaseModel):
    server_id: str
    mcp_name: str
    mcp_url: str
    override_verdict: Optional[bool] = False
    override_reason: Optional[str] = None
    actor: str = "system"
    commit_metadata: Optional[Dict[str, Any]] = {}

class HealthResponse(BaseModel):
    status: str
    service: str
    uptime_seconds: float

start_time = time.time()

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "uptime_seconds": round(time.time() - start_time, 1)}

@app.get("/commit_log")
def get_commit_log(limit: int = 50):
    rows = ws_query(f"SELECT * FROM mcp_aidr_commit_log ORDER BY created_at DESC LIMIT ?", {"p1": limit})
    return {"rows": rows}

@app.post("/commit")
def commit_mcp(req: CommitRequest, override_token: Optional[str] = Header(None)):
    commit_id = f"cmt-{uuid.uuid4().hex[:12]}"
    log(f"Commit request: commit_id={commit_id} server_id={req.server_id} actor={req.actor}")

    verdict_info = check_inference_router(req.server_id)
    verdict = verdict_info.get("verdict", "UNKNOWN")
    trust_score = verdict_info.get("trust_score", 0.0)
    log(f"Verdict check: server_id={req.server_id} verdict={verdict} trust_score={trust_score} source={verdict_info.get('source')}")

    injection_score = get_signal_score(req.server_id, "injection_resilience") or 0.0
    log(f"injection_resilience score for {req.server_id}: {injection_score}")

    if verdict in PROHIBITED_VERDICTS and not req.override_verdict:
        detail = f"COMMIT BLOCKED: verdict={verdict} is in prohibited list. Requires explicit override."
        log(f"COMMIT BLOCKED: {detail}")
        log_commit_decision(commit_id, req.server_id, verdict, injection_score, "BLOCKED", False, req.actor, detail)
        raise HTTPException(status_code=403, detail=detail)

    if verdict == RISK_OVERRIDE_THRESHOLD or req.override_verdict:
        if not req.override_verdict:
            detail = f"Verdict={verdict} requires review. No override provided. Commit deferred."
            log(f"COMMIT DEFERRED: {detail}")
            log_commit_decision(commit_id, req.server_id, verdict, injection_score, "DEFERRED", False, req.actor, detail)
            raise HTTPException(status_code=403, detail=detail)
        else:
            reason = req.override_reason or "explicit_override"
            detail = f"Override accepted: verdict={verdict} actor={req.actor} reason={reason}"
            log(f"COMMIT OVERRIDE: {detail}")

    commit_payload = {
        "server_id": req.server_id,
        "mcp_name": req.mcp_name,
        "mcp_url": req.mcp_url,
        "verdict": verdict,
        "trust_score": trust_score,
        "signals": {
            "injection_resilience_score": injection_score,
            "verdict_source": verdict_info.get("source", "db")
        },
        "metadata": req.commit_metadata,
        "commit_id": commit_id
    }

    if req.override_verdict:
        commit_payload["override"] = {"actor": req.actor, "reason": req.override_reason}

    aidr_result = forward_to_aidr(req.server_id, commit_payload, req.actor)

    if aidr_result.get("ok"):
        final_status = "COMMITTED"
        if req.override_verdict:
            final_status = "COMMITTED_WITH_OVERRIDE"
        log(f"AIDR commit SUCCESS: commit_id={commit_id} status={final_status}")
        log_commit_decision(commit_id, req.server_id, verdict, injection_score, final_status, req.override_verdict, req.actor, f"AIDR commit successful, commit_id={aidr_result.get('commit_id')}")
        return {"ok": True, "commit_id": commit_id, "aidr_commit_id": aidr_result.get("commit_id"), "verdict": verdict, "injection_resilience_score": injection_score, "status": final_status}
    else:
        detail = f"AIDR commit failed: {aidr_result.get('error')}"
        log(f"AIDR commit FAILED: {detail}")
        log_commit_decision(commit_id, req.server_id, verdict, injection_score, "AIDR_FAILED", req.override_verdict, req.actor, detail)
        raise HTTPException(status_code=502, detail=detail)

@app.get("/verdict/{server_id}")
def check_verdict(server_id: str):
    verdict_info = check_inference_router(server_id)
    injection_score = get_signal_score(server_id, "injection_resilience")
    return {"server_id": server_id, "verdict": verdict_info.get("verdict"), "trust_score": verdict_info.get("trust_score"), "injection_resilience_score": injection_score, "source": verdict_info.get("source")}

def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()

def run():
    check_single_instance()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    ensure_tables()
    log(f"{SERVICE_NAME} starting on port {PORT}")
    import threading
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    uvicorn.run(app, host="127.0.0.1", port=PORT)

if __name__ == "__main__":
    run()