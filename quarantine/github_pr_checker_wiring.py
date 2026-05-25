#!/usr/bin/env python3
"""
github_pr_checker_wiring.py -- GitHub PR Checker Webhook Wiring Daemon.
Listens for GitHub webhook events, extracts MCP server identifiers,
queries mcp_server_registry, and posts status checks back to GitHub.
"""
import os
import json
import hmac
import hashlib
import logging
import requests
import time
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, HTTPException, Header
import uvicorn

SERVICE_NAME = "github_pr_checker_wiring"
SERVICE_PORT = 8785
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(Sname)

app = FastAPI()

start_time = time.time()


def check_single_instance() -> bool:
    """Check if another instance is already running."""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                log.warning(f"Another instance already running with PID {old_pid}")
                return False
            else:
                os.remove(PID_FILE)
                log.info(f"Removed stale PID file for PID {old_pid}")
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Error checking PID file: {e}")
        return False


def remove_pid_file():
    """Remove the PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    exit(0)


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": {"service": SERVICE_NAME, "last_heartbeat": time.time()}
            },
            timeout=10
        )
    except Exception as e:
        log.error(f"Failed to send heartbeat: {e}")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query the write_service query endpoint."""
    try:
        r = requests.post(f"{WRITE_SERVICE_URL}/query", json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            result = r.json()
            return result.get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows},
            timeout=30
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return False


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature using HMAC."""
    if not signature or not secret:
        return True
    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature)


def get_github_headers() -> Dict[str, str]:
    """Get GitHub API headers with authentication token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.warning("GITHUB_TOKEN environment variable not set")
        return {"Accept": "application/vnd.github.v3+json"}
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }


def extract_mcp_identifiers_from_pr(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract MCP server identifiers from PR description and file changes."""
    identifiers = []
    
    # Extract from PR description
    pr_body = payload.get("pull_request", {}).get("body", "") or ""
    npm_pattern = r"(?:npmjs\.com\/package|npm\.im\/|github\.com\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_-]+|@[\w-]+\/[\w-]+)"
    matches = list(set(re.findall(npm_pattern, pr_body, re.IGNORECASE)))
    for m in matches:
        identifiers.append({"source": "description", "identifier": m, "type": "inferred"})
    
    # Extract from file changes (package.json additions)
    files = payload.get("pull_request", {}).get("changed_files", 0)
    for file in payload.get("pull_request", {}).get("files", [])[:20]:
        filename = file.get("filename", "")
        if "package.json" in filename.lower():
            patch = file.get("patch", "")
            additions = file.get("additions", 0)
            if additions > 0:
                identifiers.append({
                    "source": "file",
                    "identifier": filename,
                    "type": "package_json"
                })
    
    return identifiers


def parse_npm_identifier(identifier: str) -> Optional[Dict[str, str]]:
    """Parse NPM identifier into name and source."""
    identifier = identifier.strip()
    
    # Handle @scope/package format
    if identifier.startswith("@"):
        parts = identifier.split("/")
        if len(parts) >= 2:
            return {"name": parts[1], "scope": parts[0], "source": "npm"}
    
    # Handle github.com/repo format
    if "github.com" in identifier:
        match = re.search(r"github\.com\/([a-zA-Z0-9_-]+)\/([a-zA-Z0-9_-]+)", identifier)
        if match:
            return {"name": match.group(2), "owner": match.group(1), "source": "github"}
    
    # Handle npmjs.com/package format
    match = re.search(r"package\/([a-zA-Z0-9_-]+)", identifier)
    if match:
        return {"name": match.group(1), "source": "npm"}
    
    # Treat as raw package name
    if identifier:
        return {"name": identifier, "source": "npm"}
    
    return None


def lookup_server_in_registry(name: str) -> Optional[Dict[str, Any]]:
    """Look up MCP server by name in registry."""
    sql = f"SELECT server_id, name, url, description, trust_score, verdict FROM mcp_server_registry WHERE name ILIKE '%{name}%' LIMIT 1"
    results = ws_query(sql)
    if results:
        return results[0]
    
    # Try URL-based lookup
    sql = f"SELECT server_id, name, url, description, trust_score, verdict FROM mcp_server_registry WHERE url ILIKE '%{name}%' LIMIT 1"
    results = ws_query(sql)
    if results:
        return results[0]
    
    return None


def get_verdict_emoji(verdict: str) -> str:
    """Get emoji for verdict."""
    emoji_map = {
        "TRUSTED": "✅",
        "CAUTION": "⚠️",
        "HIGH_RISK": "🚨",
        "INSUFFICIENT": "❓",
        "UNKNOWN": "❓",
        "UNASSESSED": "❓",
    }
    return emoji_map.get(verdict, "❓")


def get_verdict_color(verdict: str) -> str:
    """Get color for verdict."""
    color_map = {
        "TRUSTED": "green",
        "CAUTION": "yellow",
        "HIGH_RISK": "red",
        "INSUFFICIENT": "gray",
        "UNKNOWN": "gray",
        "UNASSESSED": "gray",
    }
    return color_map.get(verdict, "gray")


def build_status_check_body(server_data: Dict[str, Any], identifier: str) -> Dict[str, Any]:
    """Build GitHub status check body."""
    verdict = server_data.get("verdict", "UNKNOWN")
    score = server_data.get("trust_score")
    name = server_data.get("name", identifier)
    
    description = f"MCP Server: {name}"
    if verdict:
        description = f"{get_verdict_emoji(verdict)} Verdict: {verdict}"
    if score is not None:
        description += f" (score: {score:.2f})"
    
    return {
        "state": "success" if verdict in ["TRUSTED", "UNASSESSED"] else ("error" if verdict == "HIGH_RISK" else "warning"),
        "target_url": f"http://127.0.0.1:8790/server/{server_data.get('server_id', '')}",
        "description": description[:140],
        "context": "ZO-SENTINEL/mcp-safety"
    }


def post_status_check(repo: str, sha: str, body: Dict[str, Any]) -> bool:
    """Post status check to GitHub API."""
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        log.warning("Cannot post status check: GITHUB_TOKEN not set")
        return False
    
    url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        r = requests.post(url, json=body, headers=headers, timeout=30)
        if r.status_code in (201, 200):
            log.info(f"Posted status check for {repo}@{sha[:7]}")
            return True
        else:
            log.error(f"Failed to post status check: {r.status_code} {r.text}")
            return False
    except Exception as e:
        log.error(f"Error posting status check: {e}")
        return False


def record_webhook_event(event_type: str, repo: str, pr_number: int, identifier: str, found: bool, verdict: str):
    """Record webhook event in audit log."""
    try:
        ws_write("audit_log", {
            "event_type": "github_pr_check",
            "actor": "github_pr_checker_wiring",
            "target_server_id": identifier[:100] if identifier else None,
            "detail": json.dumps({
                "event_type": event_type,
                "repo": repo,
                "pr_number": pr_number,
                "identifier": identifier,
                "found_in_registry": found,
                "verdict": verdict
            }),
            "created_at": time.time()
        })
    except Exception as e:
        log.error(f"Failed to record webhook event: {e}")


@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None), x_hub_signature_256: str = Header(None)):
    """Handle GitHub webhook events."""
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"Event type {x_github_event} not handled"}
    
    try:
        payload = await request.json()
    except Exception as e:
        log.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Verify signature if WEBHOOK_SECRET is set
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if secret:
        body = await request.body()
        if not verify_github_signature(body, x_hub_signature_256, secret):
            log.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        action = payload.get("action", "")
        if action not in ["opened", "synchronize", "reopened"]:
            return {"status": "ignored", "reason": f"Action {action} not processed"}
        
        pr = payload.get("pull_request", {})
        repo = payload.get("repository", {}).get("full_name", "")
        pr_number = pr.get("number", 0)
        pr_sha = pr.get("head", {}).get("sha", "")
        
        if not repo or not pr_sha:
            return {"status": "error", "reason": "Missing repo or SHA"}
        
        # Extract MCP identifiers
        identifiers = extract_mcp_identifiers_from_pr(payload)
        
        if not identifiers:
            log.info(f"No MCP identifiers found in PR #{pr_number}")
            return {"status": "ok", "identifiers_found": 0}
        
        log.info(f"Found {len(identifiers)} identifiers in PR #{pr_number}: {identifiers}")
        
        results = []
        for ident in identifiers[:10]:
            parsed = parse_npm_identifier(ident["identifier"])
            if not parsed:
                continue
            
            name = parsed.get("name", "")
            if not name:
                continue
            
            server_data = lookup_server_in_registry(name)
            
            if server_data:
                verdict = server_data.get("verdict", "UNKNOWN")
                status_body = build_status_check_body(server_data, name)
                success = post_status_check(repo, pr_sha, status_body)
                results.append({
                    "identifier": ident["identifier"],
                    "found": True,
                    "verdict": verdict,
                    "status_posted": success
                })
                record_webhook_event("pull_request", repo, pr_number, name, True, verdict)
            else:
                results.append({
                    "identifier": ident["identifier"],
                    "found": False,
                    "verdict": "UNASSESSED",
                    "status_posted": False
                })
                record_webhook_event("pull_request", repo, pr_number, name, False, "UNASSESSED")
                log.info(f"MCP server '{name}' not found in registry")
        
        return {"status": "ok", "results": results}
    
    except Exception as e:
        log.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": time.time() - start_time
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": SERVICE_NAME,
        "port": SERVICE_PORT,
        "webhook_endpoint": "/webhook",
        "health_endpoint": "/health"
    }


def heartbeat_loop():
    """Send heartbeat periodically."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


import threading
import re


def run():
    """Run the GitHub PR checker webhook service."""
    if not check_single_instance():
        log.error("Cannot start: another instance is running")
        return
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)


if __name__ == "__main__":
    run()