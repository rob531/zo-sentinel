#!/usr/bin/env python3
"""
ZO-SENTINEL Phase 9: AiDr Commit Gateway Integration
Wires aidr_commit_gateway.py to enforce verdict-check before forwarding commits.
Queries trust_synthesiser for composite verdict, rejects risky verdicts,
forwards only TRUSTED_GENERAL, TRUSTED_RESEARCH, or ENTERPRISE_CONTROLLED.
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import fcntl
import signal
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import asyncio

sys.path.insert(0, '/home/workspace/zo_sentinel')
os.chdir('/home/workspace/zo_sentinel')

SERVICE_NAME = "aidr_commit_gateway_integration"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
AIDR_GATEWAY_URL = "http://127.0.0.1:8784"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

VERDICTS_SAFE = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]
VERDICTS_REJECTED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "MALICIOUS", "KNOWN_THREAT"]
VERDICT_BLOCK_THRESHOLD = 45
INJECTION_RESILIENCE_THRESHOLD = 0.75
HEARTBEAT_INTERVAL = 30
start_time = time.time()

os.makedirs(LOG_DIR := '/home/workspace/zo_sentinel/logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/aidr_commit_gateway_integration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI()

def send_heartbeat():
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat()
            },
            "wait": True
        }, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Heartbeat failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Heartbeat error: {e}")

def check_single_instance():
    pid = str(os.getpid())
    try:
        pf = open(PID_FILE, 'r')
        existing = pf.read().strip()
        if existing and os.path.exists(f'/proc/{existing}'):
            logger.error(f"Service already running as PID {existing}")
            sys.exit(1)
    except FileNotFoundError:
        pass
    try:
        with open(PID_FILE, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.write(pid)
    except IOError:
        logger.error("Cannot acquire PID lock")
        sys.exit(1)

def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

class CommitRequest(BaseModel):
    server_id: str
    commit_hash: str
    repository: str
    branch: str
    author: str
    message: str
    files_changed: List[str]
    force_commit: bool = False
    override_reason: Optional[str] = None

class CommitResponse(BaseModel):
    commit_id: str
    status: str
    verdict: str
    injection_resilience_score: float
    blocked: bool
    message: str

def ws_query(sql: str) -> Dict[str, Any]:
    resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    return resp.status_code == 200

def check_verdict(server_id: str) -> Dict[str, Any]:
    signals = ws_query(f"""
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
    """)
    
    injection_resilience_score = 0.0
    trust_score = 0.0
    verdict = "HIGH_RISK_ISOLATED"
    
    signal_map = {}
    for row in signals.get('rows', []):
        signal_map[row['signal_name']] = row['score']
    
    if 'injection_resilience' in signal_map:
        injection_resilience_score = signal_map['injection_resilience']
    
    if 'composite' in signal_map:
        trust_score = signal_map['composite']
    elif 'trust_composite' in signal_map:
        trust_score = signal_map['trust_composite']
    else:
        weights = {
            'domain_trust': 0.20,
            'tool_description_safety': 0.20,
            'permission_scope': 0.15,
            'supply_chain': 0.15,
            'community_signal': 0.15,
            'temporal_stability': 0.15
        }
        total = 0.0
        for name, weight in weights.items():
            if name in signal_map:
                total += signal_map[name] * weight
        if total > 0:
            trust_score = total
    
    if trust_score >= 75:
        verdict = "TRUSTED_GENERAL"
    elif trust_score >= 60:
        verdict = "TRUSTED_RESEARCH"
    elif trust_score >= 45:
        verdict = "ENTERPRISE_CONTROLLED"
    elif trust_score >= 30:
        verdict = "CAUTION_LIMITED"
    elif trust_score >= 15:
        verdict = "HIGH_RISK_ISOLATED"
    else:
        verdict = "KNOWN_THREAT"
    
    return {
        'injection_resilience_score': injection_resilience_score,
        'trust_score': trust_score,
        'verdict': verdict
    }

def check_override(server_id: str, action: str = 'force_commit') -> bool:
    result = ws_query(f"""
        SELECT id FROM mcp_decisions
        WHERE server_id = '{server_id}'
        AND action = '{action}'
        AND status = 'approved'
        AND (expires_at IS NULL OR expires_at > NOW())
        LIMIT 1
    """)
    return result.get('count', 0) > 0

def process_commit(request: CommitRequest, auth_header: Optional[str]) -> tuple:
    verdict_data = check_verdict(request.server_id)
    injection_score = verdict_data['injection_resilience_score']
    trust_score = verdict_data['trust_score']
    verdict = verdict_data['verdict']
    
    registry_check = ws_query(f"""
        SELECT server_id, verdict FROM mcp_server_registry
        WHERE server_id = '{request.server_id}'
        LIMIT 1
    """)
    
    if registry_check.get('count', 0) == 0:
        logger.warning(f"Server {request.server_id} not found in registry")
        return CommitResponse(
            commit_id="",
            status="blocked",
            verdict="UNKNOWN",
            injection_resilience_score=injection_score,
            blocked=True,
            message=f"Server {request.server_id} not registered in ZO-SENTINEL"
        ), None, True
    
    if verdict in ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"] and not request.force_commit:
        reason = f"Verdict {verdict} (score={trust_score:.2f}) requires explicit override"
        if request.force_commit and request.override_reason:
            if check_override(request.server_id, 'force_commit'):
                logger.info(f"Override granted for {request.server_id}")
            else:
                reason += " - No override found in mcp_decisions"
                return CommitResponse(
                    commit_id="",
                    status="blocked",
                    verdict=verdict,
                    injection_resilience_score=injection_score,
                    blocked=True,
                    message=reason
                ), None, True
        else:
            return CommitResponse(
                commit_id="",
                status="blocked",
                verdict=verdict,
                injection_resilience_score=injection_score,
                blocked=True,
                message=reason
            ), None, True
    
    if trust_score > 0 and trust_score < VERDICT_BLOCK_THRESHOLD:
        reason = f"Trust score {trust_score:.2f} below threshold {VERDICT_BLOCK_THRESHOLD}"
        if request.force_commit and request.override_reason:
            if check_override(request.server_id, 'force_commit'):
                logger.info(f"Override granted for {request.server_id}")
            else:
                reason += " - No override found in mcp_decisions"
                return CommitResponse(
                    commit_id="",
                    status="blocked",
                    verdict=verdict,
                    injection_resilience_score=injection_score,
                    blocked=True,
                    message=reason
                ), None, True
        else:
            return CommitResponse(
                commit_id="",
                status="blocked",
                verdict=verdict,
                injection_resilience_score=injection_score,
                blocked=True,
                message=reason
            ), None, True
    
    if verdict in VERDICTS_REJECTED:
        reason = f"Verdict {verdict} is on rejection list"
        if request.force_commit and request.override_reason:
            if check_override(request.server_id, 'force_commit'):
                logger.info(f"Override granted for {request.server_id}")
            else:
                reason += " - No override found in mcp_decisions"
                return CommitResponse(
                    commit_id="",
                    status="blocked",
                    verdict=verdict,
                    injection_resilience_score=injection_score,
                    blocked=True,
                    message=reason
                ), None, True
        else:
            return CommitResponse(
                commit_id="",
                status="blocked",
                verdict=verdict,
                injection_resilience_score=injection_score,
                blocked=True,
                message=reason
            ), None, True
    
    payload = {
        "server_id": request.server_id,
        "commit_hash": request.commit_hash,
        "repository": request.repository,
        "branch": request.branch,
        "author": request.author,
        "message": request.message,
        "files_changed": request.files_changed,
        "injection_resilience_score": injection_score,
        "composite_trust_score": trust_score,
        "composite_verdict": verdict,
        "allowed_to_forward": True,
        "rejection_reason": None
    }
    
    try:
        headers = {}
        if auth_header:
            headers['Authorization'] = auth_header
        resp = requests.post(
            f"{AIDR_GATEWAY_URL}/commit",
            json=payload,
            headers=headers,
            timeout=60
        )
        if resp.status_code == 200:
            return CommitResponse(
                commit_id=payload.get('commit_id', request.commit_hash),
                status="forwarded",
                verdict=verdict,
                injection_resilience_score=injection_score,
                blocked=False,
                message="Commit forwarded to AiDr"
            ), payload, False
        else:
            return CommitResponse(
                commit_id=request.commit_hash,
                status="forward_error",
                verdict=verdict,
                injection_resilience_score=injection_score,
                blocked=True,
                message=f"AiDr gateway error: {resp.status_code}"
            ), payload, True
    except requests.exceptions.RequestException as e:
        return CommitResponse(
            commit_id=request.commit_hash,
            status="forward_error",
            verdict=verdict,
            injection_resilience_score=injection_score,
            blocked=True,
            message=f"Failed to forward: {str(e)}"
        ), payload, True

def audit_commit_decision(server_id: str, verdict: str, blocked: bool, detail: str):
    try:
        ws_write("audit_log", {
            "target_server_id": server_id,
            "event_type": "commit_verdict_check",
            "actor": SERVICE_NAME,
            "detail": json.dumps({
                "verdict": verdict,
                "blocked": blocked,
                "detail": detail
            }),
            "created_at": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Audit log failed: {e}")

@app.post("/commit", response_model=CommitResponse)
async def commit_endpoint(request: CommitRequest, authorization: Optional[str] = Header(None)):
    logger.info(f"Received commit request for server_id={request.server_id}")
    
    response, payload, blocked = process_commit(request, authorization)
    
    if blocked:
        audit_commit_decision(
            request.server_id,
            response.verdict,
            True,
            response.message
        )
        logger.warning(f"Commit BLOCKED for {request.server_id}: {response.message}")
        raise HTTPException(status_code=403, detail=response.message)
    
    audit_commit_decision(
        request.server_id,
        response.verdict,
        False,
        "Commit forwarded to AiDr"
    )
    logger.info(f"Commit ALLOWED for {request.server_id}: verdict={response.verdict}")
    
    return response

@app.get("/verdict/{server_id}")
async def get_verdict(server_id: str):
    logger.info(f"Checking verdict for server_id={server_id}")
    verdict_data = check_verdict(server_id)
    return {
        "server_id": server_id,
        "verdict": verdict_data['verdict'],
        "trust_score": verdict_data['trust_score'],
        "injection_resilience_score": verdict_data['injection_resilience_score'],
        "can_forward": verdict_data['verdict'] in VERDICTS_SAFE,
        "checked_at": datetime.now(timezone.utc).isoformat()
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": int(time.time() - start_time)
    }

def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    logger.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host='127.0.0.1', port=PORT)

if __name__ == '__main__':
    run()