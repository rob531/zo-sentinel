#!/usr/bin/env python3
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

LOG_FILE = "/tmp/github_pr_webhook.log"
SERVICE_NAME = "github_pr_webhook_wiring"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
GITHUB_API_BASE = "https://api.github.com"
PID_FILE = "/tmp/github_pr_webhook_wiring.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(SERVICE_NAME)


class GitHubWebhookPayload(BaseModel):
    action: str
    number: int
    pull_request: dict
    repository: dict
    installation: Optional[dict] = None


app = FastAPI()
start_time = time.time()
github_token = os.environ.get("GITHUB_TOKEN", "")


def check_single_instance():
    """Ensure only one instance runs."""
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = f.read().strip()
        if existing and existing != pid:
            try:
                os.kill(int(existing), 0)
                log.error(f"Another instance running with PID {existing}")
                return False
            except OSError:
                pass
    with open(PID_FILE, "w") as f:
        f.write(pid)
    log.info(f"Acquired PID file: {PID_FILE}")
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
        log.info("Removed PID file")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    raise SystemExit(0)


try:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
except Exception:
    pass


def ws_query(sql: str) -> dict:
    """Query write_service via POST /query."""
    resp = httpx.post(
        QUERY_SERVICE_URL,
        json={"sql": sql},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list) -> dict:
    """Write to write_service via POST /write."""
    resp = httpx.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    """Execute DDL/DML via write_service."""
    resp = httpx.post(
        EXECUTE_SERVICE_URL,
        json={"sql": sql},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from GitHub webhook."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def get_github_headers():
    """Build GitHub API headers."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {github_token}",
        "User-Agent": f"{SERVICE_NAME}/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return headers


def extract_mcp_name(title: str, body: str) -> Optional[str]:
    """Extract MCP server name from PR title or body."""
    combined = f"{title}\n{body or ''}"
    patterns = [
        r"(?:mcp[:\s]+|MCP[:\s]+)([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)",
        r"(?:server|package)[:\s]+([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)",
        r"@([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)",
        r"npmjs\.com/(?:package/)?@?([a-zA-Z0-9_.-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return match.group(1)
    if "mcp" in combined.lower():
        words = re.findall(r"[a-zA-Z0-9_.-]+", combined)
        for i, word in enumerate(words):
            if word.lower() in ("mcp", "mcp-server", "mcp_server"):
                if i + 1 < len(words):
                    return words[i + 1]
    return None


def get_server_verdict(mcp_name: str) -> tuple:
    """Query for server verdict by name."""
    sql = f"""
        SELECT server_id, name, verdict, trust_score
        FROM mcp_server_registry
        WHERE name ILIKE '%{mcp_name}%'
           OR name = '{mcp_name}'
        LIMIT 1
    """
    try:
        result = ws_query(sql)
        if result.get("rows") and len(result["rows"]) > 0:
            row = result["rows"][0]
            return (
                row.get("server_id"),
                row.get("name"),
                row.get("verdict"),
                row.get("trust_score"),
            )
    except Exception as e:
        log.error(f"Query error for {mcp_name}: {e}")
    return None, None, None, None


def determine_status_state(verdict: str, trust_score: float) -> dict:
    """Determine GitHub status check state and description."""
    if verdict and verdict.startswith("TRUSTED_"):
        return {
            "state": "success",
            "status": "passed",
            "description": f"MCP server verified: {verdict} (trust_score={trust_score:.2f})",
        }
    elif verdict == "ENTERPRISE_CONTROLLED":
        return {
            "state": "success",
            "status": "passed",
            "description": f"MCP server enterprise controlled: {verdict} (trust_score={trust_score:.2f})",
        }
    elif verdict in ("CAUTION_LIMITED", "RESTRICTED", "QUARANTINED", "BANNED"):
        return {
            "state": "failure",
            "status": "failed",
            "description": f"MCP server blocked: {verdict} (trust_score={trust_score:.2f})",
        }
    else:
        return {
            "state": "pending",
            "status": "pending",
            "description": f"MCP server pending review: {verdict or 'UNKNOWN'} (trust_score={trust_score:.2f})",
        }


async def create_commit_status(
    repo_full_name: str,
    sha: str,
    state: str,
    description: str,
    installation_id: int,
    context: str = "zo-sentinel/mcp-verdict",
):
    """Create GitHub commit status check via API."""
    if not github_token:
        log.warning("GITHUB_TOKEN not set, skipping status check")
        return None
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/statuses/{sha}"
    headers = get_github_headers()
    payload = {
        "state": state,
        "target_url": "https://zo-sentinel.example.com/registry",
        "description": description[:140] if len(description) > 140 else description,
        "context": context,
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url, headers=headers, json=payload, timeout=30.0
            )
            if resp.status_code == 201:
                log.info(f"Created status check for {repo_full_name}@sha={sha[:7]}: {state}")
                return resp.json()
            else:
                log.error(f"Status check failed: {resp.status_code} {resp.text}")
                return None
        except Exception as e:
            log.error(f"Error creating status check: {e}")
            return None


def write_audit_log(
    event_type: str,
    target_server_id: Optional[str],
    actor: str,
    detail: dict,
) -> bool:
    """Write audit log entry for PR event."""
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    sql_check = """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at TIMESTAMP
        )
    """
    try:
        ws_execute(sql_check)
    except Exception as e:
        log.debug(f"Table check: {e}")
    sql = f"""
        INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at)
        VALUES (
            {repr(target_server_id) if target_server_id else 'NULL'},
            {repr(event_type)},
            {repr(actor)},
            {repr(json.dumps(detail))},
            {repr(now)}
        )
    """
    try:
        ws_write("audit_log", [{
            "target_server_id": target_server_id,
            "event_type": event_type,
            "actor": actor,
            "detail": json.dumps(detail),
            "created_at": now,
        }])
        log.info(f"Audit log written: {event_type} by {actor}")
        return True
    except Exception as e:
        log.error(f"Audit log write failed: {e}")
        return False


@app.post("/github-webhook")
async def handle_github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
    x_github_delivery: Optional[str] = Header(None),
    x_github_hook_installation_target_id: Optional[str] = Header(None),
):
    """Handle incoming GitHub PR webhook events."""
    log.info(f"Received webhook: event={x_github_event}, delivery={x_github_delivery}")
    if x_github_event != "pull_request":
        log.info(f"Ignoring non-PR event: {x_github_event}")
        return {"status": "ignored", "event": x_github_event}
    payload_bytes = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if secret and x_hub_signature_256:
        if not verify_signature(payload_bytes, x_hub_signature_256, secret):
            log.error("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        log.error(f"Invalid JSON payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    if not pr or not repo:
        raise HTTPException(status_code=400, detail="Missing PR or repository data")
    title = pr.get("title", "")
    body = pr.get("body", "")
    sha = pr.get("head", {}).get("sha", "")
    pr_number = pr.get("number", 0)
    repo_full_name = repo.get("full_name", "")
    actor = payload.get("sender", {}).get("login", "unknown")
    installation_id_str = x_github_hook_installation_target_id or str(
        payload.get("installation", {}).get("id", "")
    )
    try:
        installation_id = int(installation_id_str) if installation_id_str else 0
    except ValueError:
        installation_id = 0
    mcp_name = extract_mcp_name(title, body)
    detail = {
        "action": action,
        "pr_number": pr_number,
        "repo": repo_full_name,
        "sha": sha,
        "actor": actor,
        "mcp_name_found": mcp_name,
        "title": title[:200],
        "delivery": x_github_delivery,
    }
    if not mcp_name:
        log.info(f"No MCP name found in PR #{pr_number} from {repo_full_name}")
        write_audit_log(
            "github_pr_unmatched",
            None,
            actor,
            detail,
        )
        return {
            "status": "unmatched",
            "message": "No MCP server name found in PR",
            "pr": pr_number,
            "repo": repo_full_name,
        }
    server_id, matched_name, verdict, trust_score = get_server_verdict(mcp_name)
    if not server_id:
        log.info(f"MCP server not found in registry: {mcp_name}")
        write_audit_log(
            "github_pr_not_found",
            None,
            actor,
            {**detail, "mcp_name_searched": mcp_name},
        )
        return {
            "status": "not_found",
            "mcp_name": mcp_name,
            "message": "MCP server not in registry",
            "pr": pr_number,
            "repo": repo_full_name,
        }
    state_info = determine_status_state(verdict, trust_score or 0.0)
    detail.update({
        "server_id": server_id,
        "matched_name": matched_name,
        "verdict": verdict,
        "trust_score": trust_score,
        "status_state": state_info["state"],
    })
    if github_token and sha and repo_full_name:
        status_result = await create_commit_status(
            repo_full_name,
            sha,
            state_info["state"],
            state_info["description"],
            installation_id,
        )
        detail["status_check_created"] = status_result is not None
    write_audit_log(
        "github_pr_verdict_check",
        server_id,
        actor,
        detail,
    )
    log.info(
        f"PR #{pr_number} verdict check: {mcp_name} -> {verdict} ({state_info['state']})"
    )
    return {
        "status": state_info["status"],
        "mcp_name": mcp_name,
        "verdict": verdict,
        "trust_score": trust_score,
        "description": state_info["description"],
        "pr": pr_number,
        "repo": repo_full_name,
        "sha": sha[:7],
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": int(time.time() - start_time),
    }


@app.get("/verify-signature")
async def verify_sig(
    payload: str,
    signature: str,
):
    """Debug endpoint to test signature verification."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "testsecret")
    is_valid = verify_signature(payload.encode(), signature, secret)
    return {"valid": is_valid, "signature": signature}


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }])
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


def run():
    """Main run function for daemon mode."""
    if not check_single_instance():
        log.error("Failed to acquire PID file, exiting")
        return
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    send_heartbeat()
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="info",
        )
    except Exception as e:
        log.error(f"Server error: {e}")
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()