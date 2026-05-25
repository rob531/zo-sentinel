#!/usr/bin/env python3
"""
github_pr_checker_wiring_v3.py -- GitHub PR Checker Webhook Wiring Daemon.
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

SERVICE_NAME = "github_pr_checker_wiring_v3"
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
log = logging.getLogger(SERVICE_NAME)

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
            log.info("Removed PID file")
    except Exception as e:
        log.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    exit(0)


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query the write_service query endpoint."""
    try:
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service."""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        r = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        if r.status_code == 200:
            return True
        log.error(f"ws_write failed: {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"ws_write error: {e}")
    return False


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not signature or not secret:
        return False
    try:
        expected = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        log.error(f"Signature verification error: {e}")
        return False


def get_github_headers() -> Dict[str, str]:
    """Get GitHub API headers with authentication token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.warning("GITHUB_TOKEN environment variable not set")
        return {"Accept": "application/vnd.github+json"}
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }


def lookup_mcp_server(server_identifier: str) -> Optional[Dict[str, Any]]:
    """Look up an MCP server in the registry by name or URL."""
    sql = f"""
        SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count
        FROM mcp_server_registry
        WHERE name ILIKE '%{server_identifier}%'
           OR url ILIKE '%{server_identifier}%'
        LIMIT 1
    """
    results = ws_query(sql)
    return results[0] if results else None


def get_server_signals(server_id: str) -> List[Dict[str, Any]]:
    """Get all signal scores for a server."""
    sql = f"""
        SELECT signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
    """
    return ws_query(sql)


def get_server_threats(server_id: str) -> List[Dict[str, Any]]:
    """Get threat associations for a server."""
    sql = f"""
        SELECT threat_type, severity, evidence, reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
    """
    return ws_query(sql)


def get_risk_register(server_id: str) -> Optional[Dict[str, Any]]:
    """Get risk tier information for a server."""
    sql = f"""
        SELECT risk_tier, risk_rank, threat_count, computed_at
        FROM mcp_risk_register
        WHERE server_id = '{server_id}'
        LIMIT 1
    """
    results = ws_query(sql)
    return results[0] if results else None


def extract_mcp_identifiers_from_diff(diff_content: str) -> List[str]:
    """Extract MCP server identifiers from PR diff content."""
    identifiers = set()
    
    patterns = [
        r'"name"\s*:\s*"(@[^"]+|[^"]+mcp[^"]+)"',
        r'npmjs\.com\/package\/(@?[\w-]+)',
        r'github\.com\/([^\/]+)\/([^\/\s]+)',
        r'"url"\s*:\s*"(https?:\/\/[^\"]+)"',
        r'mcp-server-([\w-]+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, diff_content, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                identifiers.update([m for m in match if m])
            else:
                identifiers.add(match)
    
    return list(identifiers)


def extract_mcp_identifiers_from_files(files: List[Dict[str, Any]]) -> List[str]:
    """Extract MCP server identifiers from PR file list."""
    identifiers = set()
    
    for file_info in files:
        filename = file_info.get("filename", "")
        
        if "package.json" in filename or "mcp_config" in filename or "mcp.json" in filename:
            content = file_info.get("patch", "") or ""
            identifiers.update(extract_mcp_identifiers_from_diff(content))
        
        if "mcp" in filename.lower():
            identifiers.add(filename.replace("/", "_").replace(".json", ""))
    
    return list(identifiers)


def parse_pr_url(pr_url: str) -> Optional[tuple]:
    """Parse a GitHub PR URL into owner, repo, pr_number components."""
    match = re.match(r"github\.com\/([^\/]+)\/([^\/]+)\/pull\/(\d+)", pr_url, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2), int(match.group(3))
    return None


def fetch_pr_details(owner: str, repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
    """Fetch PR details from GitHub API."""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error(f"Error fetching PR details: {e}")
    return None


def fetch_pr_files(owner: str, repo: str, pr_number: int) -> List[Dict[str, Any]]:
    """Fetch files changed in a PR."""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error(f"Error fetching PR files: {e}")
    return []


def post_pr_comment(owner: str, repo: str, pr_number: int, body: str) -> bool:
    """Post a comment to a GitHub PR."""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
        payload = {"body": body}
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        return r.status_code == 201
    except Exception as e:
        log.error(f"Error posting comment: {e}")
        return False


def create_safety_assessment(server: Dict[str, Any], signals: List[Dict], threats: List[Dict], risk: Optional[Dict]) -> str:
    """Create a formatted safety assessment comment."""
    verdict = server.get("verdict", "UNKNOWN")
    trust_score = server.get("trust_score", 0.0)
    name = server.get("name", "Unknown")
    url = server.get("url", "")
    
    verdict_emoji = {
        "TRUSTED": "✅", "CAUTION": "⚠️", "HIGH_RISK": "🚨",
        "UNKNOWN": "❓", "UNASSESSED": "❓", "INSUFFICIENT": "❓"
    }.get(verdict, "❓")
    
    lines = [
        f"## 🔍 ZO-SENTINEL Safety Assessment",
        "",
        f"**Server:** {name}",
        f"**Verdict:** {verdict_emoji} {verdict}",
        f"**Trust Score:** {trust_score:.2f}",
    ]
    
    if url:
        lines.append(f"**URL:** {url}")
    
    if risk:
        lines.append(f"**Risk Tier:** {risk.get('risk_tier', 'UNKNOWN')}")
    
    if threats:
        lines.append(f"**Threats:** {len(threats)} identified")
        for threat in threats[:3]:
            lines.append(f"  - [{threat.get('severity', '?')}] {threat.get('threat_type', 'unknown')}")
    
    if signals:
        lines.append("**Top Signals:**")
        for sig in signals[:5]:
            lines.append(f"  - {sig.get('signal_name', 'unknown')}: {sig.get('score', 0.0):.2f}")
    
    lines.append("")
    lines.append("_Assessment by ZO-SENTINEL MCP Safety Intelligence_")
    
    return "\n".join(lines)


def get_github_webhook_secret() -> str:
    """Get the GitHub webhook secret from environment."""
    return os.environ.get("GITHUB_WEBHOOK_SECRET", "")


@app.post("/webhook")
async def github_webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    """Handle GitHub webhook events."""
    payload = await request.body()
    secret = get_github_webhook_secret()
    
    if secret and x_hub_signature_256:
        if not verify_github_signature(payload, x_hub_signature_256, secret):
            log.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        event_data = json.loads(payload)
    except json.JSONDecodeError:
        log.error("Invalid JSON in webhook payload")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_action = event_data.get("action", "")
    pr_info = event_data.get("pull_request", {})
    pr_url = pr_info.get("html_url", "")
    
    if "opened" not in event_action and "synchronize" not in event_action:
        return {"status": "ignored", "reason": f"action={event_action}"}
    
    parsed = parse_pr_url(pr_url)
    if not parsed:
        log.warning(f"Could not parse PR URL: {pr_url}")
        return {"status": "error", "reason": "Could not parse PR URL"}
    
    owner, repo, pr_number = parsed
    
    files = fetch_pr_files(owner, repo, pr_number)
    identifiers = extract_mcp_identifiers_from_files(files)
    
    if not identifiers:
        return {"status": "ok", "assessed": 0, "servers": []}
    
    results = []
    for identifier in identifiers:
        server = lookup_mcp_server(identifier)
        if server:
            server_id = server.get("server_id")
            signals = get_server_signals(server_id)
            threats = get_server_threats(server_id)
            risk = get_risk_register(server_id)
            
            assessment = create_safety_assessment(server, signals, threats, risk)
            
            if post_pr_comment(owner, repo, pr_number, assessment):
                ws_write("audit_log", {
                    "target_server_id": server_id,
                    "event_type": "github_pr_check",
                    "actor": "github_pr_checker_wiring",
                    "detail": f"Posted assessment to PR #{pr_number} for {server.get('name')}",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                results.append({"identifier": identifier, "server": server.get("name"), "verdict": server.get("verdict")})
    
    log.info(f"Assessed {len(results)} MCP servers from PR #{pr_number}")
    return {"status": "ok", "assessed": len(results), "servers": results}


@app.get("/health")
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": time.time() - start_time
    }


@app.get("/")
def root():
    """Root endpoint."""
    return {"service": SERVICE_NAME, "status": "running"}


def heartbeat_loop():
    """Background heartbeat loop."""
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()


import threading


def run():
    """Main entry point for the daemon."""
    import signal
    
    if not check_single_instance():
        log.error("Another instance is already running. Exiting.")
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()