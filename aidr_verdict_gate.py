import sys
import os
import signal
import time
import uuid
import hashlib
import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
import uvicorn
import requests

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "aidr_verdict_gate"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
AIDR_COMMIT_GATEWAY_URL = "http://127.0.0.1:8787"
HEARTBEAT_INTERVAL = 30
VERDICT_QUERY_TIMEOUT = 30
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

VERDICTS_ALLOWED_AUTO_COMMIT = ["TRUSTED", "REVIEW_PASSED", "LOW_RISK", "UNREVIEWED"]
VERDICTS_BLOCKED = ["KNOWN_THREAT", "HIGH_RISK"]
VERDICTS_OVERRIDE_REQUIRED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]

app = FastAPI()

start_time = time.time()

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_URL

def get_execute_url():
    return EXECUTE_URL

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        response = requests.post(
            get_query_url(),
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"rows": [], "count": 0, "error": str(e)}

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        response = requests.post(
            get_write_url(),
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        response = requests.post(
            get_execute_url(),
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            print(f"{SERVICE_NAME} already running with PID {old_pid}")
            return False
        else:
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception:
        pass

def get_verdict_for_server(server_id: str) -> Optional[Dict[str, Any]]:
    try:
        sql = f"""
        SELECT server_id, verdict, trust_score, computed_at
        FROM mcp_risk_register
        WHERE server_id = '{server_id}'
        ORDER BY computed_at DESC
        LIMIT 1
        """
        result = ws_query(sql)
        if result.get("rows") and len(result["rows"]) > 0:
            return result["rows"][0]
        return None
    except Exception as e:
        print(f"Error querying verdict: {e}")
        return None

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    try:
        sql = f"""
        SELECT score
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
        ORDER BY scored_at DESC
        LIMIT 1
        """
        result = ws_query(sql)
        if result.get("rows") and len(result["rows"]) > 0:
            return result["rows"][0].get("score")
        return None
    except Exception as e:
        print(f"Error querying injection_resilience score: {e}")
        return None

def log_commit_decision(
    server_id: str,
    decision: str,
    verdict: Optional[str],
    override_used: bool,
    reason: str,
    commit_payload: Optional[Dict[str, Any]] = None
):
    try:
        audit_entry = {
            "id": str(uuid.uuid4()),
            "target_server_id": server_id,
            "event_type": f"aidr_commit_gate_{decision}",
            "actor": SERVICE_NAME,
            "detail": json.dumps({
                "verdict": verdict,
                "override_used": override_used,
                "reason": reason,
                "commit_forwarded": commit_payload is not None,
                "timestamp": datetime.utcnow().isoformat()
            }),
            "created_at": datetime.utcnow().isoformat()
        }
        ws_write("audit_log", [audit_entry])
    except Exception as e:
        print(f"Error logging commit decision: {e}")

def can_auto_commit(verdict: Optional[str]) -> bool:
    if verdict is None:
        return True
    return verdict in VERDICTS_ALLOWED_AUTO_COMMIT

def requires_override(verdict: Optional[str]) -> bool:
    if verdict is None:
        return False
    return verdict in VERDICTS_OVERRIDE_REQUIRED

def is_blocked(verdict: Optional[str]) -> bool:
    if verdict is None:
        return False
    return verdict in VERDICTS_BLOCKED

async def forward_to_aidr_commit_gateway(commit_payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = requests.post(
            AIDR_COMMIT_GATEWAY_URL,
            json=commit_payload,
            timeout=30
        )
        response.raise_for_status()
        return {"ok": True, "response": response.json()}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Timeout forwarding to AIDR commit gateway"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def validate_commit_payload(payload: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    if not payload.get("server_id"):
        return False, "Missing server_id in commit payload"
    if not payload.get("commit_data"):
        return False, "Missing commit_data in commit payload"
    return True, None

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": int(time.time() - start_time)
    }

@app.post("/commit")
async def handle_commit(request: Request, x_override_token: Optional[str] = Header(None)):
    body = await request.json()
    server_id = body.get("server_id")
    commit_data = body.get("commit_data", {})
    force_forward = body.get("force_forward", False)

    is_valid, error_msg = validate_commit_payload({"server_id": server_id, "commit_data": commit_data})
    if not is_valid:
        log_commit_decision(
            server_id=server_id or "unknown",
            decision="rejected",
            verdict=None,
            override_used=False,
            reason=f"Invalid payload: {error_msg}"
        )
        raise HTTPException(status_code=400, detail=error_msg)

    verdict_data = None
    try:
        loop = asyncio.get_event_loop()
        verdict_data = await asyncio.wait_for(
            loop.run_in_executor(None, get_verdict_for_server, server_id),
            timeout=VERDICT_QUERY_TIMEOUT
        )
    except asyncio.TimeoutError:
        log_commit_decision(
            server_id=server_id,
            decision="rejected",
            verdict=None,
            override_used=False,
            reason="Verdict query timeout exceeded (30s)"
        )
        raise HTTPException(status_code=504, detail="Verdict query timeout")

    verdict = verdict_data.get("verdict") if verdict_data else None

    if is_blocked(verdict):
        log_commit_decision(
            server_id=server_id,
            decision="blocked",
            verdict=verdict,
            override_used=False,
            reason=f"Verdict {verdict} is explicitly blocked from forwarding"
        )
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": f"Cannot forward commit: server has verdict '{verdict}' which is blocked",
                "verdict": verdict,
                "action": "BLOCKED"
            }
        )

    injection_resilience_score = None
    try:
        loop = asyncio.get_event_loop()
        injection_resilience_score = await asyncio.wait_for(
            loop.run_in_executor(None, get_injection_resilience_score, server_id),
            timeout=10
        )
    except asyncio.TimeoutError:
        print(f"Timeout querying injection_resilience score for {server_id}")

    override_used = False
    if requires_override(verdict):
        override_token_valid = False
        if x_override_token:
            override_hash = hashlib.sha256(x_override_token.encode()).hexdigest()[:16]
            override_token_valid = force_forward and len(x_override_token) >= 32
            if override_token_valid:
                override_used = True

        if not override_used:
            log_commit_decision(
                server_id=server_id,
                decision="blocked_override_required",
                verdict=verdict,
                override_used=False,
                reason=f"Verdict {verdict} requires explicit override token to forward"
            )
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": f"Cannot auto-forward commit: server has verdict '{verdict}' requiring explicit override",
                    "verdict": verdict,
                    "action": "OVERRIDE_REQUIRED",
                    "hint": "Include X-Override-Token header with valid override authorization"
                }
            )

    if not can_auto_commit(verdict) and not override_used:
        log_commit_decision(
            server_id=server_id,
            decision="rejected",
            verdict=verdict,
            override_used=False,
            reason=f"Verdict {verdict} not in allowed auto-commit list"
        )
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": f"Cannot forward commit: verdict '{verdict}' not allowed for auto-commit",
                "verdict": verdict,
                "action": "REJECTED"
            }
        )

    commit_payload = {
        "server_id": server_id,
        "commit_data": commit_data,
        "verdict": verdict,
        "injection_resilience_score": injection_resilience_score,
        "verdict_source": "zo_sentinel_gate",
        "forwarded_at": datetime.utcnow().isoformat(),
        "override_used": override_used
    }

    forward_result = await forward_to_aidr_commit_gateway(commit_payload)

    if forward_result.get("ok"):
        log_commit_decision(
            server_id=server_id,
            decision="forwarded",
            verdict=verdict,
            override_used=override_used,
            reason="Commit successfully forwarded to AIDR gateway",
            commit_payload=commit_payload
        )
        return {
            "ok": True,
            "verdict": verdict,
            "injection_resilience_score": injection_resilience_score,
            "forwarded": True,
            "override_used": override_used
        }
    else:
        log_commit_decision(
            server_id=server_id,
            decision="forward_failed",
            verdict=verdict,
            override_used=override_used,
            reason=f"Failed to forward: {forward_result.get('error')}"
        )
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": f"Failed to forward to AIDR gateway: {forward_result.get('error')}",
                "verdict": verdict
            }
        )

@app.post("/check/{server_id}")
async def check_verdict(server_id: str):
    verdict_data = None
    try:
        loop = asyncio.get_event_loop()
        verdict_data = await asyncio.wait_for(
            loop.run_in_executor(None, get_verdict_for_server, server_id),
            timeout=VERDICT_QUERY_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Verdict query timeout")

    verdict = verdict_data.get("verdict") if verdict_data else None

    injection_resilience_score = None
    try:
        loop = asyncio.get_event_loop()
        injection_resilience_score = await asyncio.wait_for(
            loop.run_in_executor(None, get_injection_resilience_score, server_id),
            timeout=10
        )
    except asyncio.TimeoutError:
        pass

    can_forward = can_auto_commit(verdict) or requires_override(verdict)
    is_blocked_verdict = is_blocked(verdict)

    return {
        "server_id": server_id,
        "verdict": verdict,
        "trust_score": verdict_data.get("trust_score") if verdict_data else None,
        "injection_resilience_score": injection_resilience_score,
        "can_forward": can_forward and not is_blocked_verdict,
        "requires_override": requires_override(verdict),
        "is_blocked": is_blocked_verdict,
        "computed_at": verdict_data.get("computed_at") if verdict_data else None
    }

@app.post("/batch-check")
async def batch_check(request: Request):
    body = await request.json()
    server_ids = body.get("server_ids", [])

    if len(server_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 server IDs per batch")

    results = []
    for server_id in server_ids:
        verdict_data = None
        try:
            loop = asyncio.get_event_loop()
            verdict_data = await asyncio.wait_for(
                loop.run_in_executor(None, get_verdict_for_server, server_id),
                timeout=VERDICT_QUERY_TIMEOUT
            )
        except asyncio.TimeoutError:
            verdict_data = None

        verdict = verdict_data.get("verdict") if verdict_data else None
        verdict_status = "blocked" if is_blocked(verdict) else ("override_required" if requires_override(verdict) else ("auto_commit" if can_auto_commit(verdict) else "review_required"))

        results.append({
            "server_id": server_id,
            "verdict": verdict,
            "verdict_status": verdict_status
        })

    return {"results": results, "count": len(results)}

def ensure_tables():
    try:
        ws_execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id VARCHAR PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at TIMESTAMP
        )
        """)
    except Exception as e:
        print(f"Warning: Could not ensure audit_log table: {e}")

def run():
    if not check_single_instance():
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    ensure_tables()
    send_heartbeat()

    print(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host='127.0.0.1', port=PORT)

if __name__ == '__main__':
    run()