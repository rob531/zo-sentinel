#!/usr/bin/env python3
"""
github_pr_webhook_handler_integration.py -- ZO-SENTINEL GitHub PR webhook daemon.
Receives GitHub PR events, validates webhook signatures, extracts MCP server names,
checks the registry, and posts verdict summary comments on PRs.
"""
import os
import re
import logging
import hmac
import hashlib
import time
import signal
from typing import Dict, Any, Optional, List

import requests
from fastapi import FastAPI, Request, HTTPException, Header
import uvicorn

log = logging.getLogger(__name__)

SERVICE_NAME = "github_pr_webhook_handler"
SERVICE_PORT = 8780
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60

RISK_TIER_THRESHOLDS = {
    "trusted": 0.7,
    "caution": 0.4,
}

VERDICT_EMOJI = {
    "TRUSTED": "✅",
    "CAUTION": "⚠️",
    "HIGH_RISK": "🚨",
    "INSUFFICIENT": "❓",
    "UNKNOWN": "❓",
    "UNASSESSED": "❓",
}

VERDICT_COLOR = {
    "TRUSTED": "green",
    "CAUTION": "yellow",
    "HIGH_RISK": "red",
    "INSUFFICIENT": "gray",
    "UNKNOWN": "gray",
    "UNASSESSED": "gray",
}

MCP_NAME_PATTERNS = [
    r'@[\w-]+/[\w-]+',
    r'mcp-[\w-]+',
    r'[\w-]+-mcp',
    r'mcp_server[_-]?[\w-]+',
    r'[\w\-\.]+(?:server|client|mcp)(?:\s|$|/|:)',
    r'(?:name|repo)[:\s]+([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)',
]

app = FastAPI()

start_time = time.time()


def check_single_instance() -> bool:
    """Check if another instance is already running."""
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance is already running (PID {old_pid})")
            return False
        except (OSError, ValueError):
            log.warning(f"Stale PID file found, removing it")
            os.remove(pid_file)
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Remove the PID file on shutdown."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info("Received shutdown signal, cleaning up...")
    remove_pid_file()
    exit(0)


def ws_query(sql: str) -> list:
    """Query the write_service query endpoint."""
    try:
        r = requests.post(f"{QUERY_URL}", json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write data to write_service."""
    try:
        r = requests.post(f"{WRITE_SERVICE}/write", json={"table": table, "rows": rows, "wait": True}, timeout=30)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return False


def send_heartbeat():
    """Send heartbeat to service_health."""
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": time.time()}])
    except Exception as e:
        log.error(f"Heartbeat error: {e}")


def heartbeat_loop():
    """Loop that sends heartbeats every HEARTBEAT_INTERVAL seconds."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def get_github_headers() -> Dict[str, str]:
    """Get GitHub API headers with authentication token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable not set")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_webhook_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook signature using X-Hub-Signature-256."""
    if not signature_header:
        log.warning("No signature header provided")
        return False
    if not signature_header.startswith("sha256="):
        log.warning("Signature does not start with sha256=")
        return False
    expected_sig = signature_header[7:]
    computed_hmac = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hmac, expected_sig)


def extract_mcp_server_names(title: str, body: str) -> List[str]:
    """Extract MCP server names from PR title and body using regex patterns."""
    servers = set()
    text = f"{title} {body}"
    for pattern in MCP_NAME_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            cleaned = match.strip().rstrip('/')
            if cleaned and len(cleaned) > 2:
                servers.add(cleaned)
    return list(servers)


def parse_pr_url(github_repo: str, pr_number: int) -> tuple:
    """Parse GitHub repo info and construct API URL."""
    return github_repo, f"https://api.github.com/repos/{github_repo}/pulls/{pr_number}"


def check_server_in_registry(server_name: str) -> Optional[Dict[str, Any]]:
    """Check if server exists in mcp_server_registry."""
    sql = f"SELECT server_id, name, url, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE name = '{server_name}' LIMIT 1"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    sql_lower = f"SELECT server_id, name, url, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE LOWER(name) = LOWER('{server_name}') LIMIT 1"
    rows = ws_query(sql_lower)
    if rows:
        return rows[0]
    return None


def get_server_verdict(server_data: Dict[str, Any]) -> tuple:
    """Get verdict and risk tier from server data."""
    trust_score = server_data.get("trust_score", 0.0)
    verdict = server_data.get("verdict", "UNKNOWN")
    if trust_score >= RISK_TIER_THRESHOLDS["trusted"]:
        risk_tier = "trusted"
    elif trust_score >= RISK_TIER_THRESHOLDS["caution"]:
        risk_tier = "caution"
    else:
        risk_tier = "high_risk"
    return verdict, risk_tier, trust_score


def format_verdict_comment(server_data: Dict[str, Any], server_name: str) -> str:
    """Format verdict summary as PR comment."""
    verdict, risk_tier, trust_score = get_server_verdict(server_data)
    emoji = VERDICT_EMOJI.get(verdict, "❓")
    url = server_data.get("url", "N/A")
    source = server_data.get("registry_source", "unknown")
    scan_count = server_data.get("scan_count", 0)
    comment = f"""## 🔍 ZO-SENTINEL MCP Safety Assessment

### {emoji} Verdict: {verdict}

| Attribute | Value |
|-----------|-------|
| **Server Name** | `{server_name}` |
| **Trust Score** | `{trust_score:.2f}` |
| **Risk Tier** | `{risk_tier}` |
| **Registry Source** | `{source}` |
| **Scan Count** | `{scan_count}` |
| **URL** | [{url}]({url}) |

---
*Assessment generated by ZO-SENTINEL*
"""
    return comment


def post_pr_comment(repo: str, pr_number: int, comment_body: str) -> bool:
    """Post a comment to the GitHub PR."""
    headers = get_github_headers()
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        r = requests.post(url, json={"body": comment_body}, headers=headers, timeout=10)
        if r.status_code in (201, 200):
            log.info(f"Successfully posted comment to PR #{pr_number}")
            return True
        else:
            log.error(f"Failed to post comment: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        log.error(f"Error posting PR comment: {e}")
        return False


def check_existing_comment(repo: str, pr_number: int, server_name: str) -> Optional[int]:
    """Check if ZO-SENTINEL already commented on this PR for this server."""
    headers = get_github_headers()
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            comments = r.json()
            for comment in comments:
                if "ZO-SENTINEL" in comment.get("body", "") and server_name in comment.get("body", ""):
                    return comment.get("id")
        return None
    except Exception as e:
        log.error(f"Error checking existing comments: {e}")
        return None


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "uptime": time.time() - start_time}


@app.post("/webhook/github")
async def handle_github_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """Handle incoming GitHub PR webhook events."""
    payload = await request.body()
    github_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if github_secret and x_hub_signature_256:
        if not verify_webhook_signature(payload, x_hub_signature_256, github_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        event_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    action = event_data.get("action", "")
    pull_request = event_data.get("pull_request", {})
    if not pull_request:
        return {"status": "ignored", "reason": "No pull_request in payload"}
    if action not in ("opened", "reopened", "synchronize"):
        return {"status": "ignored", "reason": f"Action '{action}' not handled"}
    pr_title = pull_request.get("title", "")
    pr_body = pull_request.get("body", "") or ""
    pr_number = pull_request.get("number", 0)
    repo = event_data.get("repository", {}).get("full_name", "")
    if not repo:
        raise HTTPException(status_code=400, detail="Missing repository info")
    log.info(f"Processing PR #{pr_number} from {repo}: {pr_title}")
    mcp_servers = extract_mcp_server_names(pr_title, pr_body)
    if not mcp_servers:
        log.info(f"No MCP server names found in PR #{pr_number}")
        return {"status": "ignored", "reason": "No MCP server names found"}
    results = []
    for server_name in mcp_servers:
        server_data = check_server_in_registry(server_name)
        if not server_data:
            log.info(f"Server '{server_name}' not found in registry")
            results.append({"server": server_name, "status": "not_found"})
            continue
        existing_comment_id = check_existing_comment(repo, pr_number, server_name)
        comment_body = format_verdict_comment(server_data, server_name)
        success = post_pr_comment(repo, pr_number, comment_body)
        results.append({
            "server": server_name,
            "status": "success" if success else "failed",
            "verdict": server_data.get("verdict", "UNKNOWN"),
            "trust_score": server_data.get("trust_score", 0.0),
        })
    return {"status": "processed", "pr": pr_number, "repo": repo, "results": results}


def run():
    """Run the GitHub PR webhook handler daemon."""
    import threading
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not check_single_instance():
        log.error("Failed to acquire PID lock. Exiting.")
        return
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()