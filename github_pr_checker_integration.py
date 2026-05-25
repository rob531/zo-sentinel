import logging
import time
import signal
import os
import requests
import hashlib
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, HTTPException, Header
import uvicorn

SERVICE_NAME = "github_pr_checker_integration"
SERVICE_PORT = 8784
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300
START_TIME = time.time()
SHUTDOWN = False

app = FastAPI()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(SERVICE_NAME)

CAUTION_LIMITED = "CAUTION_LIMITED"
HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"

def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            logger.error(f"Another instance running with PID {old_pid}. Exiting.")
            exit(1)
        else:
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    global SHUTDOWN
    logger.info(f"Received signal {signum}, shutting down...")
    SHUTDOWN = True
    remove_pid_file()
    exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {"rows": [], "count": 0}

def ws_write(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return {"ok": False, "error": str(e)}

def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return {"ok": False, "error": str(e)}

def ensure_tables():
    webhook_events_table = """
    CREATE TABLE IF NOT EXISTS github_pr_webhook_events (
        event_id VARCHAR PRIMARY KEY,
        action VARCHAR,
        server_id VARCHAR,
        server_name VARCHAR,
        repository VARCHAR,
        pr_number INTEGER,
        sender VARCHAR,
        verdict_checked BOOLEAN,
        verdict_blocked BOOLEAN,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(webhook_events_table)
    logger.info("Webhook events table ensured")

def verify_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected_sig = signature_header[7:]
    computed = hashlib.sha256(secret.encode() + payload_bytes).hexdigest()
    return computed == expected_sig

def parse_server_id_from_diff(diff_content: str) -> Optional[str]:
    patterns = [
        r'mcp[_-]?server[_-]?id["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)',
        r'server[_-]?id["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)',
        r'mcp[_-]?server["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, diff_content, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def parse_pr_diff_files(pr_diff: str) -> List[str]:
    server_ids = []
    file_patterns = [
        r'data["\']?\s*[:=]\s*["\']([^"\']*(?:mcp|mcp-server)[^"\']*)["\']',
        r'"name":\s*"([^"]*(?:mcp|mcp-server)[^"]*)"',
        r'package\.json.*?"name":\s*"([^"]*(?:mcp|mcp-server)[^"]*)"',
    ]
    for pattern in file_patterns:
        matches = re.findall(pattern, pr_diff, re.IGNORECASE)
        server_ids.extend(matches)
    return list(set(server_ids))

def get_verdict(server_id: str) -> Optional[str]:
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1"
    result = ws_query(sql)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0].get("verdict")
    return None

def check_verdict_allowed(verdict: Optional[str]) -> bool:
    if verdict is None:
        return False
    blocked_verdicts = [CAUTION_LIMITED, HIGH_RISK_ISOLATED]
    return verdict not in blocked_verdicts

def record_webhook_event(event_id: str, action: str, server_id: str, server_name: str,
                          repository: str, pr_number: int, sender: str,
                          verdict_checked: bool, verdict_blocked: bool):
    sql = f"""
    INSERT INTO github_pr_webhook_events (event_id, action, server_id, server_name, repository, pr_number, sender, verdict_checked, verdict_blocked)
    VALUES ('{event_id}', '{action}', '{server_id}', '{server_name}', '{repository}', {pr_number}, '{sender}', {verdict_checked}, {verdict_blocked})
    """
    ws_write(sql)

def log_pr_event(event_type: str, server_id: str, verdict: Optional[str], decision: str, details: str = ""):
    audit_sql = f"""
    INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at)
    VALUES ('{server_id}', '{event_type}', 'github_pr_webhook', 'PR {decision}: verdict={verdict}, details={details}', CURRENT_TIMESTAMP)
    """
    ws_write(audit_sql)

def process_pr_addition(server_id: str, server_name: str, url: str, description: str, repository: str, pr_number: int):
    verdict = get_verdict(server_id)
    allowed = check_verdict_allowed(verdict)
    
    if not allowed:
        logger.warning(f"Verdict check failed for server_id={server_id} with verdict={verdict}, rejecting PR")
        log_pr_event("pr_rejected", server_id, verdict, "REJECTED", f"Verdict {verdict} blocked addition")
        return {
            "status": "rejected",
            "reason": "verdict_blocked",
            "server_id": server_id,
            "verdict": verdict,
            "message": f"PR rejected: MCP has verdict {verdict}, which is blocked"
        }
    
    existing = ws_query(f"SELECT server_id, name, url FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if existing.get("rows") and len(existing["rows"]) > 0:
        sql = f"UPDATE mcp_server_registry SET name = '{server_name}', url = '{url}', description = '{description}', registry_source = 'github_pr' WHERE server_id = '{server_id}'"
    else:
        sql = f"""
        INSERT INTO mcp_server_registry (server_id, name, url, description, trust_score, verdict, registry_source, scan_count)
        VALUES ('{server_id}', '{server_name}', '{url}', '{description}', 0.5, 'CANDIDATE', 'github_pr', 0)
        """
    
    ws_write(sql)
    log_pr_event("pr_approved", server_id, verdict, "APPROVED", f"Added via PR #{pr_number} from {repository}")
    
    return {
        "status": "approved",
        "server_id": server_id,
        "verdict": verdict,
        "message": "PR approved: MCP added/updated successfully"
    }

def process_pr_modification(server_id: str, server_name: str, url: str, description: str, repository: str, pr_number: int):
    verdict = get_verdict(server_id)
    allowed = check_verdict_allowed(verdict)
    
    if not allowed:
        logger.warning(f"Verdict check failed for server_id={server_id} with verdict={verdict}, rejecting modification")
        log_pr_event("pr_rejected", server_id, verdict, "REJECTED", f"Verdict {verdict} blocked modification")
        return {
            "status": "rejected",
            "reason": "verdict_blocked",
            "server_id": server_id,
            "verdict": verdict,
            "message": f"PR modification rejected: MCP has verdict {verdict}, which is blocked"
        }
    
    sql = f"UPDATE mcp_server_registry SET name = '{server_name}', url = '{url}', description = '{description}' WHERE server_id = '{server_id}'"
    ws_write(sql)
    log_pr_event("pr_approved", server_id, verdict, "APPROVED", f"Modified via PR #{pr_number} from {repository}")
    
    return {
        "status": "approved",
        "server_id": server_id,
        "verdict": verdict,
        "message": "PR modification approved"
    }

def process_pr_removal(server_id: str, repository: str, pr_number: int):
    verdict = get_verdict(server_id)
    sql = f"UPDATE mcp_server_registry SET verdict = 'DEPRECATED' WHERE server_id = '{server_id}'"
    ws_write(sql)
    log_pr_event("pr_approved", server_id, verdict, "APPROVED", f"Removed via PR #{pr_number} from {repository}")
    
    return {
        "status": "approved",
        "server_id": server_id,
        "message": "PR removal approved: MCP marked deprecated"
    }

@app.post("/webhook/github")
async def github_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    try:
        payload_bytes = await request.body()
        payload = await request.json()
        
        event_type = request.headers.get("x-github-event", "unknown")
        action = payload.get("action", "unknown")
        pr = payload.get("pull_request", {})
        repository = payload.get("repository", {}).get("full_name", "unknown")
        pr_number = pr.get("number", 0)
        sender = payload.get("sender", {}).get("login", "unknown")
        
        event_id = f"{repository}-pr-{pr_number}-{int(time.time())}"
        
        logger.info(f"Processing GitHub webhook: event={event_type}, action={action}, repo={repository}, PR={pr_number}")
        
        if event_type not in ("pull_request", "pull_request_review", "pull_request_review_comment"):
            return {"status": "ignored", "message": f"Event type {event_type} not handled"}
        
        if action not in ("opened", "synchronize", "closed", "reopened"):
            return {"status": "ignored", "message": f"Action {action} not processed"}
        
        server_id = pr.get("title", "") + "-" + pr.get("head", {}).get("sha", "")[:8]
        server_id = hashlib.md5(server_id.encode()).hexdigest()[:16]
        
        server_name = pr.get("title", f"PR-{pr_number}")
        url = pr.get("html_url", f"https://github.com/{repository}/pull/{pr_number}")
        description = pr.get("body", "")[:500] if pr.get("body") else ""
        
        if action == "closed" and pr.get("merged", False):
            result = process_pr_addition(server_id, server_name, url, description, repository, pr_number)
        elif action == "closed":
            result = process_pr_removal(server_id, repository, pr_number)
        else:
            result = process_pr_modification(server_id, server_name, url, description, repository, pr_number)
        
        verdict_checked = True
        verdict_blocked = result.get("status") == "rejected"
        record_webhook_event(event_id, action, server_id, server_name, repository, pr_number, sender, verdict_checked, verdict_blocked)
        
        return result
        
    except Exception as e:
        logger.error(f"GitHub webhook processing error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health():
    try:
        test_query = ws_query("SELECT 1 as test")
        write_ok = ws_write("SELECT 1")
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "uptime": int(time.time() - START_TIME),
            "write_service": "ok" if write_ok.get("ok") is not False else "error",
            "query_service": "ok" if test_query else "error"
        }
    except Exception as e:
        return {
            "status": "error",
            "service": SERVICE_NAME,
            "uptime": int(time.time() - START_TIME),
            "error": str(e)
        }

@app.get("/verdict-check/{server_id}")
async def verdict_check(server_id: str):
    verdict = get_verdict(server_id)
    allowed = check_verdict_allowed(verdict)
    return {
        "server_id": server_id,
        "verdict": verdict,
        "allowed": allowed,
        "blocked_verdicts": [CAUTION_LIMITED, HIGH_RISK_ISOLATED]
    }

def heartbeat_loop():
    while not SHUTDOWN:
        try:
            sql = f"SELECT '{SERVICE_NAME}' as service, '{datetime.now().isoformat()}' as last_heartbeat"
            requests.post(WRITE_SERVICE_URL, json={"table": "service_health", "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.now().isoformat()}]}, timeout=10)
        except Exception as e:
            logger.error(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)

def run():
    check_single_instance()
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    ensure_tables()
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)
    finally:
        remove_pid_file()

if __name__ == "__main__":
    run()