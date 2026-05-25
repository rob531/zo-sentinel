import logging
import time
import os
import sys
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
import hashlib
import hmac
import json

SERVICE_NAME = "github_pr_checker_wiring"
PORT = 8785
POLL_SECS = 30
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
GITHUB_API_TOKEN = os.environ.get("GITHUB_API_TOKEN", "")
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

app = FastAPI()
log = logging.getLogger(SERVICE_NAME)

def check_single_instance():
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        if os.path.exists(f"/proc/{old_pid}"):
            log.warning(f"Another instance running with PID {old_pid}")
            sys.exit(0)
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

def send_heartbeat():
    try:
        import requests
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": int(time.time())
            },
            "wait": True
        }, timeout=5)
        if resp.status_code != 200:
            log.warning(f"Heartbeat failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"Heartbeat error: {e}")

def verify_github_signature(payload_bytes: bytes, signature: str) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        return True
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

def extract_mcp_server_id(pr_body: str, changed_files: list) -> str:
    """Extract MCP server identifier from PR description or files."""
    body_lower = pr_body.lower() if pr_body else ""
    for line in body_lower.split('\n'):
        line = line.strip()
        if line.startswith('server:') or line.startswith('mcp-server:'):
            return line.split(':', 1)[1].strip()
        if 'mcp-server:' in line:
            parts = line.split('mcp-server:', 1)
            if len(parts) > 1:
                return parts[1].strip().split()[[i for i, p in enumerate(parts) if 'mcp-server:' in p][0]]
    patterns = ['mcp-server-', '/mcp-servers/', 'mcp-servers/']
    for f in changed_files:
        f_lower = f.lower()
        for pat in patterns:
            if pat in f_lower:
                parts = f_lower.split(pat, 1)
                if len(parts) > 1:
                    server_id = parts[1].split('/')[0].split('.')[0]
                    if server_id:
                        return server_id
    return None

def lookup_server_in_registry(server_id: str) -> dict:
    """Query mcp_server_registry via write_service."""
    try:
        import requests
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json={
            "sql": f"SELECT server_id, name, description, trust_score, verdict FROM mcp_server_registry WHERE server_id = '{server_id}' OR name ILIKE '%{server_id}%' LIMIT 5"
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        return []
    except Exception as e:
        log.error(f"Registry lookup failed: {e}")
        return []

def post_pr_status_check(repo: str, sha: str, state: str, description: str):
    """Post status check back to GitHub via API."""
    if not GITHUB_API_TOKEN:
        log.warning("GITHUB_API_TOKEN not set, skipping status post")
        return
    try:
        import requests
        headers = {
            "Authorization": f"token {GITHUB_API_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
        payload = {
            "state": state,
            "description": description[:140],
            "context": "zo-sentinel/mcp-check"
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code in (201, 200):
            log.info(f"Posted status check: {state}")
        else:
            log.warning(f"Status check failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log.error(f"Posting status check failed: {e}")

class WebhookPayload(BaseModel):
    action: str = ""
    pull_request: dict = None
    repository: dict = None

@app.post("/webhook")
async def github_webhook(request: Request):
    """Receive and process GitHub webhook events."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_github_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    event = request.headers.get("X-GitHub-Event", "")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if event == "pull_request":
        pr = payload.get("pull_request", {})
        repo = payload.get("repository", {}).get("full_name", "")
        pr_body = pr.get("body", "")
        files_url = pr.get("url", "")
        changed_files = []
        if GITHUB_API_TOKEN and files_url:
            try:
                import requests
                headers = {"Authorization": f"token {GITHUB_API_TOKEN}"}
                files_resp = requests.get(files_url, headers=headers, timeout=10)
                if files_resp.status_code == 200:
                    for f in files_resp.json():
                        changed_files.append(f.get('filename', ''))
            except Exception:
                pass
        server_id = extract_mcp_server_id(pr_body, changed_files)
        if server_id:
            log.info(f"Extracted server_id: {server_id}")
            matches = lookup_server_in_registry(server_id)
            if matches:
                verdict = matches[0].get('verdict', 'unknown')
                trust_score = matches[0].get('trust_score', 0)
                if verdict == 'approved' and trust_score >= 80:
                    state = "success"
                    desc = f"ZO-SENTINEL: Server '{server_id}' verified (trust: {trust_score})"
                elif verdict == 'blocked' or trust_score < 30:
                    state = "failure"
                    desc = f"ZO-SENTINEL: Server '{server_id}' flagged (trust: {trust_score})"
                else:
                    state = "pending"
                    desc = f"ZO-SENTINEL: Server '{server_id}' under review (trust: {trust_score})"
                sha = pr.get('head', {}).get('sha', '')
                post_pr_status_check(repo, sha, state, desc)
                return {"status": "processed", "server_id": server_id, "verdict": verdict}
            else:
                log.info(f"No registry match for: {server_id}")
                sha = pr.get('head', {}).get('sha', '')
                post_pr_status_check(repo, sha, "neutral", f"ZO-SENTINEL: Server '{server_id}' not in registry")
                return {"status": "processed", "server_id": server_id, "verdict": "not_found"}
        return {"status": "skipped", "reason": "no_server_id_found"}
    return {"status": "ignored", "event": event}

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "port": PORT}

@app.get("/")
def root():
    return {"service": SERVICE_NAME, "version": "1.0", "port": PORT}

def run():
    check_single_instance()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    send_heartbeat()
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level="info")

if __name__ == '__main__':
    run()