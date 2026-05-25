#!/usr/bin/env python3
"""
github_pr_checker_wiring_v2.py -- ZO-SENTINEL GitHub PR webhook wiring v2.
Receives GitHub PR webhook events, validates HMAC signature, extracts MCP server
names from PR body/config files via regex, queries mcp_server_registry for verdicts,
and posts GitHub check run results with retry logic (3 tries, exponential backoff).
References github_pr_checker_webhook_wiring.py for write_service patterns.
"""
import os
import re
import sys
import hmac
import hashlib
import logging
import time
import signal
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "github_pr_checker_wiring_v2.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
    ]
)
log = logging.getLogger(__name__)

SERVICE_NAME = "github_pr_checker_wiring_v2"
PORT = 8787
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
WRITE_URL = "http://localhost:8772/write"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

app = FastAPI()
START_TIME = datetime.now(timezone.utc)

RETRY_MAX_TRIES = 3
RETRY_BASE_DELAY = 2.0


def ws_query(sql: str) -> list:
    """Query write_service SELECT endpoint with timeout."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=30
        )
        if r.status_code == 200:
            result = r.json()
            return result.get("rows", [])
        log.error(f"ws_query failed: {r.status_code} - {r.text}")
    except Exception as e:
        log.error(f"ws_query exception: {e}")
    return []


def ws_write(table: str, rows: list) -> bool:
    """Write to write_service via POST /write."""
    try:
        r = requests.post(
            f"{WRITE_URL}",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        if r.status_code == 200:
            return True
        log.error(f"ws_write failed: {r.status_code} - {r.text}")
    except Exception as e:
        log.error(f"ws_write exception: {e}")
    return False


def get_github_headers() -> Dict[str, str]:
    """Get GitHub API headers with authentication token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.warning("GITHUB_TOKEN not set; API calls may be rate-limited")
        return {"Accept": "application/vnd.github+json"}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }


def verify_github_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not signature_header or not secret:
        log.warning("Missing signature header or webhook secret")
        return False
    mac = hmac.new(
        secret.encode('utf-8'),
        payload_body,
        hashlib.sha256
    )
    expected = f"sha256={mac.hexdigest()}"
    return hmac.compare_digest(expected, signature_header)


def extract_mcp_servers_from_pr_body(pr_body: str) -> List[str]:
    """Extract MCP server names from PR description/body."""
    servers = set()
    if not pr_body:
        return []
    patterns = [
        r'(?:mcp[-_]server|server)[-_]?name[:\s]+([a-zA-Z0-9_./-]+)',
        r'(?:npm|npmjs)[.@/]([a-zA-Z0-9_./-]+)',
        r'(?:github|github\.com)/([a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]+)',
        r'@([a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, pr_body, re.IGNORECASE)
        for match in matches:
            if len(match) > 2:
                servers.add(match.strip())
    return list(servers)


def extract_mcp_servers_from_config_files(files: List[Dict[str, Any]]) -> List[str]:
    """Extract MCP server references from config files in PR."""
    servers = set()
    config_patterns = [
        r'mcpServers?\s*[=:]\s*\{([^}]+)\}',
        r'(?:mcp[-_]server|server)[-_]?name[:\s]+([a-zA-Z0-9_./:-]+)',
        r'(?:url|command)[:\s]+([^\s,}]+)',
    ]
    config_files = ['mcp.json', 'mcp_config.json', '.mcp.json', 'mcp.yaml', '.mcp.yaml']
    for f in files:
        filename = f.get('filename', '')
        if any(cf in filename.lower() for cf in config_files):
            patch = f.get('patch', '')
            if patch:
                for pattern in config_patterns:
                    matches = re.findall(pattern, patch, re.IGNORECASE)
                    for match in matches:
                        servers.add(match.strip())
    return list(servers)


def get_mcp_verdict(server_name: str) -> Optional[Dict[str, Any]]:
    """Query mcp_server_registry for verdict on a server."""
    sql = f"""
        SELECT server_id, name, url, trust_score, verdict, registry_source, scan_count
        FROM mcp_server_registry
        WHERE name ILIKE '%{server_name}%'
           OR name = '{server_name}'
           OR url ILIKE '%{server_name}%'
        LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    sql = f"""
        SELECT server_id, name, url, trust_score, verdict, registry_source, scan_count
        FROM mcp_server_registry
        WHERE name ILIKE '%{server_name.replace('/', '%')}%'
        LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return None


def github_api_call_with_retry(method: str, url: str, headers: Dict[str, str],
                               payload: Optional[Dict[str, Any]] = None,
                               max_tries: int = RETRY_MAX_TRIES) -> Optional[Dict[str, Any]]:
    """Make GitHub API call with exponential backoff retry logic."""
    delay = RETRY_BASE_DELAY
    for attempt in range(max_tries):
        try:
            if method.upper() == 'POST':
                r = requests.post(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == 'PATCH':
                r = requests.patch(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == 'GET':
                r = requests.get(url, headers=headers, timeout=30)
            else:
                r = requests.request(method.upper(), url, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201, 202):
                try:
                    return r.json()
                except Exception:
                    return {"ok": True}
            elif r.status_code == 403:
                log.warning(f"GitHub API rate limited (attempt {attempt + 1}/{max_tries})")
            elif r.status_code == 404:
                log.warning(f"GitHub API resource not found: {url}")
                return None
            else:
                log.warning(f"GitHub API returned {r.status_code} (attempt {attempt + 1}/{max_tries}): {r.text[:200]}")
            if attempt < max_tries - 1:
                time.sleep(delay)
                delay *= 2
        except Exception as e:
            log.error(f"GitHub API call exception (attempt {attempt + 1}/{max_tries}): {e}")
            if attempt < max_tries - 1:
                time.sleep(delay)
                delay *= 2
    log.error(f"GitHub API call failed after {max_tries} attempts: {url}")
    return None


def create_check_run(owner: str, repo: str, sha: str, name: str,
                     status: str, conclusion: Optional[str],
                     output_title: str, output_summary: str,
                     output_text: Optional[str] = None) -> Optional[str]:
    """Create a GitHub check run and return its ID."""
    headers = get_github_headers()
    url = f"https://api.github.com/repos/{owner}/{repo}/check-runs"
    payload = {
        "name": name,
        "head_sha": sha,
        "status": status,
        "details_url": "https://sentinel.example.com",
        "external_id": f"mcp-verdict-{sha[:8]}",
    }
    if conclusion:
        payload["conclusion"] = conclusion
    if output_title or output_summary:
        payload["output"] = {
            "title": output_title,
            "summary": output_summary,
        }
        if output_text:
            payload["output"]["text"] = output_text
    result = github_api_call_with_retry('POST', url, headers, payload)
    if result and isinstance(result, dict):
        return result.get('id')
    return None


def update_check_run(check_run_id: int, owner: str, repo: str,
                     conclusion: str, output_title: str,
                     output_summary: str, output_text: Optional[str] = None) -> bool:
    """Update a GitHub check run with conclusion."""
    headers = get_github_headers()
    url = f"https://api.github.com/repos/{owner}/{repo}/check-runs/{check_run_id}"
    payload = {
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": output_title,
            "summary": output_summary,
        }
    }
    if output_text:
        payload["output"]["text"] = output_text
    result = github_api_call_with_retry('PATCH', url, headers, payload)
    return result is not None


def format_verdict_summary(verdict_data: Dict[str, Any]) -> tuple:
    """Format verdict data into GitHub check output."""
    verdict = verdict_data.get('verdict', 'UNKNOWN')
    trust_score = verdict_data.get('trust_score', 0.0)
    server_name = verdict_data.get('name', 'unknown')
    registry_source = verdict_data.get('registry_source', 'unknown')
    scan_count = verdict_data.get('scan_count', 0)
    verdict_emoji = {
        "TRUSTED": "✅",
        "AMBER": "⚠️",
        "UNTRUSTED": "🚨",
        "HIGH_RISK": "🚨",
        "KNOWN_THREAT": "🛑",
        "UNKNOWN": "❓",
    }.get(verdict, "❓")
    verdict_conclusion = {
        "TRUSTED": "success",
        "AMBER": "neutral",
        "UNTRUSTED": "failure",
        "HIGH_RISK": "failure",
        "KNOWN_THREAT": "failure",
        "UNKNOWN": "neutral",
    }.get(verdict, "neutral")
    title = f"{verdict_emoji} MCP Verdict: {verdict}"
    summary = f"**Server:** {server_name}\n**Verdict:** {verdict}\n**Trust Score:** {trust_score:.2f}\n**Registry Source:** {registry_source}\n**Scan Count:** {scan_count}"
    details = f"Verdict assessed by ZO-SENTINEL at {datetime.now(timezone.utc).isoformat()}Z\n\nRegistry Source: {registry_source}\nTrust Score: {trust_score:.2f}/1.0\nTotal Scans: {scan_count}"
    return verdict_conclusion, title, summary, details


def process_pr_webhook(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process a GitHub PR webhook event and post verdict check."""
    action = payload.get('action', '')
    if action not in ('opened', 'synchronize', 'reopened', 'ready_for_review'):
        log.info(f"Ignoring PR action: {action}")
        return {"status": "ignored", "reason": f"action={action}"}
    pr = payload.get('pull_request', {})
    if not pr:
        return {"status": "error", "reason": "no pull_request in payload"}
    pr_body = pr.get('body', '') or ''
    pr_title = pr.get('title', '') or ''
    pr_body_combined = f"{pr_title}\n\n{pr_body}"
    files_url = pr.get('url', '')
    sha = pr.get('head', {}).get('sha', '')
    repo = payload.get('repository', {})
    owner = repo.get('owner', {}).get('login', '')
    repo_name = repo.get('name', '')
    if not owner or not repo_name:
        return {"status": "error", "reason": "missing repository info"}
    all_servers = []
    all_servers.extend(extract_mcp_servers_from_pr_body(pr_body_combined))
    headers = get_github_headers()
    if files_url:
        files_resp = github_api_call_with_retry('GET', files_url, headers)
        if files_resp and isinstance(files_resp, list):
            all_servers.extend(extract_mcp_servers_from_config_files(files_resp))
    all_servers = list(set(all_servers))
    if not all_servers:
        log.info("No MCP servers found in PR")
        return {"status": "ok", "servers_found": 0, "verdicts": []}
    verdicts = []
    for server_name in all_servers:
        verdict_data = get_mcp_verdict(server_name)
        if verdict_data:
            verdicts.append(verdict_data)
    if not verdicts:
        verdict_data = {"name": ", ".join(all_servers), "verdict": "UNKNOWN",
                        "trust_score": 0.0, "registry_source": "pr_body", "scan_count": 0}
        verdicts.append(verdict_data)
    check_run_id = create_check_run(
        owner=owner, repo=repo_name, sha=sha,
        name="MCP Verdict Check",
        status="in_progress",
        conclusion=None,
        output_title="🔍 Scanning MCP packages...",
        output_summary="Verdict check in progress. This may take a moment."
    )
    if not check_run_id:
        check_run_id = 0
    safe_title = f"✅ {len(verdicts)} MCP Verdict(s) Assessed"
    safe_summary = f"Found {len(all_servers)} MCP server(s) in PR. {len(verdicts)} verdict(s) retrieved.\n\n"
    safe_details_lines = []
    has_untrusted = False
    has_high_risk = False
    for vd in verdicts:
        vdata = {k: str(v) for k, v in vd.items()}
        verdict = vdata.get('verdict', 'UNKNOWN')
        trust_score = float(vdata.get('trust_score', 0.0))
        server_name = vdata.get('name', 'unknown')
        registry_source = vdata.get('registry_source', 'unknown')
        scan_count = int(vdata.get('scan_count', 0))
        safe_summary += f"- **{server_name}**: {verdict} (score: {trust_score:.2f})\n"
        safe_details_lines.append(
            f"Server: {server_name}\n  Verdict: {verdict}\n  Trust Score: {trust_score:.2f}\n  "
            f"Source: {registry_source}\n  Scans: {scan_count}\n"
        )
        if verdict in ('UNTRUSTED', 'HIGH_RISK', 'KNOWN_THREAT'):
            has_untrusted = True
            has_high_risk = True
        elif verdict == 'AMBER' and not has_high_risk:
            has_untrusted = True
    conclusion = "success"
    if has_high_risk:
        conclusion = "failure"
    elif has_untrusted:
        conclusion = "neutral"
    safe_details = "\n".join(safe_details_lines)
    if check_run_id > 0:
        update_check_run(check_run_id, owner, repo_name, conclusion,
                         safe_title, safe_summary, safe_details)
    else:
        log.info(f"Check run created for {owner}/{repo_name}@{sha[:8]}: {len(verdicts)} verdicts")
    return {
        "status": "ok",
        "servers_found": len(all_servers),
        "verdicts_retrieved": len(verdicts),
        "check_conclusion": conclusion,
        "check_run_id": check_run_id
    }


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_hub_event: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
):
    """Handle incoming GitHub webhook events."""
    body = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if secret and x_hub_signature_256:
        if not verify_github_signature(body, x_hub_signature_256, secret):
            log.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except Exception as e:
        log.error(f"Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    event = x_hub_event or "unknown"
    log.info(f"Received GitHub webhook: event={event} delivery={x_github_delivery}")
    if event not in ('pull_request', 'ping'):
        log.info(f"Ignoring non-PR event: {event}")
        return JSONResponse({"status": "ignored", "event": event})
    if event == 'ping':
        return JSONResponse({"status": "ok", "message": "Pong!"})
    try:
        result = process_pr_webhook(event, payload)
        return JSONResponse(result)
    except Exception as e:
        log.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": (datetime.now(timezone.utc) - START_TIME).total_seconds()
    }


def check_single_instance():
    """Ensure only one instance of this service is running."""
    if os.path.exists(PID_FILE):
        old_pid = None
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
        except Exception:
            pass
        if old_pid:
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance is running with PID {old_pid}")
                sys.exit(1)
            except OSError:
                pass
        os.unlink(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove the PID file on shutdown."""
    try:
        os.unlink(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    """Send heartbeat to service_health table."""
    ts = datetime.now(timezone.utc).isoformat()
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "meta": json.dumps({"port": PORT})
    }]
    ws_write("service_health", rows)


def run():
    """Start the FastAPI webhook service."""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()