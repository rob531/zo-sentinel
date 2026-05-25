#!/usr/bin/env python3
"""
github_pr_checker_webhook_wiring.py -- ZO-SENTINEL GitHub PR webhook integration.
Receives GitHub PR webhooks, validates HMAC signature, extracts MCP server
identifiers from PR title/labels, queries mcp_server_registry.verdict via
write_service, and posts GitHub PR status check (pending/success/failure)
with verdict summary.

Requires GITHUB_TOKEN environment variable for API calls.
Webhooks must include X-Hub-Signature-256 header for authenticity validation.
"""
import os
import hmac
import hashlib
import logging
import signal
import sys
import time
import uuid
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
import uvicorn

SERVICE_NAME = "github_pr_checker_webhook_wiring"
PORT = 8795
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
GITHUB_API_URL = "https://api.github.com"
WEBHOOK_SECRET_ENV = "GITHUB_WEBHOOK_SECRET"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

app = FastAPI()


def ws_query(sql: str) -> list:
    """Query write_service /query endpoint."""
    try:
        r = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def ws_write(table: str, rows: list) -> bool:
    """Write rows to write_service /write endpoint."""
    try:
        r = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write error: {e}")
    return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service /execute endpoint."""
    try:
        r = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_execute error: {e}")
    return False


def send_heartbeat() -> None:
    """Send heartbeat to service_health table."""
    ts = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": ts, "status": "ok", "meta": "{}"}])


def check_single_instance() -> None:
    """Guard against multiple instances."""
    try:
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        if old_pid > 0:
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance is running (PID {old_pid}). Exiting.")
                sys.exit(1)
            except OSError:
                pass
    except FileNotFoundError:
        pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file on exit."""
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    """Handle SIGTERM/SIGINT gracefully."""
    log.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def get_github_headers() -> dict:
    """Get GitHub API headers with Bearer token."""
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if not token:
        raise ValueError(f"{GITHUB_TOKEN_ENV} environment variable not set")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_webhook_signature(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """Verify HMAC-SHA256 signature from GitHub webhook X-Hub-Signature-256 header."""
    if not signature_header:
        log.warning("No X-Hub-Signature-256 header present")
        return False
    secret = os.environ.get(WEBHOOK_SECRET_ENV)
    if not secret:
        log.warning(f"{WEBHOOK_SECRET_ENV} not set — skipping signature validation")
        return True
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_mcp_server_from_pr_payload(payload: dict) -> list:
    """Extract MCP server identifiers from PR title and labels."""
    identifiers = []
    pr_title = payload.get("pull_request", {}).get("title", "")
    pr_body = payload.get("pull_request", {}).get("body", "") or ""
    labels = [l.get("name", "") for l in payload.get("pull_request", {}).get("labels", [])]
    all_text = f"{pr_title} {pr_body}"

    npm_pattern = r"npmjs\.com/(?:package/)?(@?[a-z0-9_-]+(?:/[a-z0-9_-]+)?)"
    pypi_pattern = r"(?:pypi\.org/project/)([a-z0-9_-]+)"
    github_pattern = r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
    bare_npm = r"(?:^|[\s/])(@?[a-z][a-z0-9_-]*[a-z0-9])(?:[\s/]|$)"
    schema_pattern = r'"name"\s*:\s*"([a-zA-Z0-9_/-]+)"'

    for match in re.finditer(npm_pattern, all_text):
        identifiers.append({"source": "npm", "name": match.group(1)})
    for match in re.finditer(pypi_pattern, all_text):
        identifiers.append({"source": "pypi", "name": match.group(1)})
    for match in re.finditer(github_pattern, all_text):
        identifiers.append({"source": "github", "namespace": match.group(1), "repo": match.group(2)})
    for match in re.finditer(bare_npm, all_text):
        name = match.group(1)
        if not any(i.get("name") == name for i in identifiers):
            identifiers.append({"source": "npm", "name": name})
    for match in re.finditer(schema_pattern, all_text):
        name = match.group(1)
        if not any(i.get("name") == name for i in identifiers):
            identifiers.append({"source": "schema", "name": name})

    for label in labels:
        label_lower = label.lower()
        if label_lower.startswith("mcp-"):
            name = label_lower.replace("mcp-", "")
            identifiers.append({"source": "label", "name": name})
        elif "npm" in label_lower or "pypi" in label_lower:
            identifiers.append({"source": "label", "name": label})

    return identifiers


def query_server_verdict(server_name: str) -> dict:
    """Query mcp_server_registry for verdict, trust_score, and risk_tier."""
    sql = f"""
        SELECT server_id, name, url, description, trust_score, verdict, risk_tier, registry_source
        FROM mcp_server_registry
        WHERE name ILIKE '%{server_name}%'
           OR url ILIKE '%{server_name}%'
        ORDER BY scan_count DESC
        LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        row = rows[0]
        return {
            "found": True,
            "server_id": row.get("server_id"),
            "name": row.get("name"),
            "verdict": row.get("verdict", "UNKNOWN"),
            "trust_score": row.get("trust_score"),
            "risk_tier": row.get("risk_tier"),
            "registry_source": row.get("registry_source"),
        }
    return {"found": False}


def compute_status_conclusion(verdict: str, trust_score: Optional[float]) -> tuple:
    """Map verdict + trust_score to GitHub status check state and description."""
    verdict_upper = (verdict or "UNKNOWN").upper()
    score = trust_score if trust_score is not None else 0.0

    if verdict_upper in ("TRUSTED", "ENTERPRISE_CONTROLLED"):
        return "success", "✅ MCP verified — TRUSTED", score
    elif verdict_upper in ("HIGH_RISK", "KNOWN_THREAT"):
        return "failure", "🚨 MCP flagged — HIGH_RISK or KNOWN_THREAT", score
    elif verdict_upper == "AMBER_UNVERIFIED":
        if score >= 0.6:
            return "success", "⚠️ MCP conditionally approved — AMBER score OK", score
        return "failure", "⚠️ MCP requires manual review — AMBER_UNVERIFIED below threshold", score
    elif verdict_upper == "CAUTION_LIMITED":
        if score >= 0.7:
            return "success", "⚠️ MCP caution — passing trust threshold", score
        return "failure", "⚠️ MCP caution — CAUTION_LIMITED below threshold", score
    elif verdict_upper == "UNKNOWN":
        return "neutral", "❓ MCP not in registry — UNASSESSED", score
    else:
        return "neutral", f"❓ MCP verdict: {verdict_upper} (score {score:.2f})", score


def post_github_status_check(
    repo: str,
    sha: str,
    state: str,
    description: str,
    target_url: Optional[str] = None,
) -> Optional[dict]:
    """Post GitHub PR status check via checks API."""
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if not token:
        log.error("GITHUB_TOKEN not set")
        return None

    headers = get_github_headers()
    run_name = "ZO-Sentinel MCP Safety Check"

    payload = {
        "name": run_name,
        "head_sha": sha,
        "status": "completed",
        "conclusion": state,
        "output": {
            "title": f"MCP Safety: {state.upper()}",
            "summary": description,
        },
    }
    if target_url:
        payload["details_url"] = target_url

    try:
        r = requests.post(
            f"{GITHUB_API_URL}/repos/{repo}/check-runs",
            json=payload,
            headers=headers,
            timeout=10,
        )
        if r.status_code in (200, 201):
            log.info(f"Posted status check to {repo} @ {sha[:7]} — {state}")
            return r.json()
        else:
            log.error(f"GitHub API error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"post_github_status_check error: {e}")

    return None


def ensure_webhook_events_table() -> None:
    """Ensure webhook_events table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS github_webhook_events (
        event_id TEXT PRIMARY KEY,
        repo TEXT,
        pr_number INTEGER,
        action TEXT,
        sender TEXT,
        mcp_identifiers TEXT,
        verdicts TEXT,
        status_check_posted BOOLEAN,
        received_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming GitHub webhook for pull_request events."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", str(uuid.uuid4()))

    log.info(f"Webhook received: event={event} delivery={delivery_id}")

    if event != "pull_request":
        return {"status": "ignored", "reason": f"event={event}"}

    if not verify_webhook_signature(body, signature):
        log.warning(f"Invalid webhook signature for delivery {delivery_id}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    pr_data = payload.get("pull_request", {})
    action = payload.get("action", "")
    repo = payload.get("repository", {}).get("full_name", "")
    sender = payload.get("sender", {}).get("login", "")
    sha = pr_data.get("head", {}).get("sha", "")
    pr_number = pr_data.get("number", 0)
    title = pr_data.get("title", "")

    if action not in ("opened", "synchronize", "reopened"):
        log.info(f"Action '{action}' not relevant — skipping")
        return {"status": "ignored", "action": action}

    identifiers = extract_mcp_server_from_pr_payload(payload)
    if not identifiers:
        log.info(f"No MCP identifiers found in PR #{pr_number}")
        post_github_status_check(
            repo, sha, "neutral",
            "❓ No MCP server identifiers detected in PR.",
            target_url="https://github.com/zo-computer/zo-sentinel",
        )
        return {"status": "processed", "mcp_identifiers": [], "verdicts": []}

    verdicts = []
    for ident in identifiers:
        result = query_server_verdict(ident.get("name", ""))
        verdict_data = {**ident, **result}
        verdicts.append(verdict_data)

    all_trusted = all(v.get("verdict") in ("TRUSTED", "ENTERPRISE_CONTROLLED") for v in verdicts if v.get("found"))
    any_unknown = any(not v.get("found") for v in verdicts)
    any_high_risk = any(v.get("verdict") in ("HIGH_RISK", "KNOWN_THREAT") for v in verdicts if v.get("found"))

    if any_high_risk:
        state, desc, score = "failure", "🚨 MCP flagged as HIGH_RISK/KNOWN_THREAT — PR blocked", 0.0
    elif all_trusted:
        state, desc, score = "success", "✅ All MCP servers verified as TRUSTED", 1.0
    elif any_unknown:
        state, desc, score = "neutral", "❓ Some MCP servers not in registry — manual review recommended", 0.5
    else:
        state, desc, score = "success", "⚠️ MCP servers present — no critical findings", 0.6

    summary_lines = [f"ZO-Sentinel assessed {len(verdicts)} MCP server(s) in PR #{pr_number}"]
    for v in verdicts:
        src = v.get("source", "unknown")
        name = v.get("name", "?")
        found = v.get("found", False)
        verdict = v.get("verdict", "UNKNOWN") if found else "NOT_IN_REGISTRY"
        score_val = v.get("trust_score")
        score_str = f" (score={score_val:.2f})" if score_val is not None else ""
        summary_lines.append(f"  [{src}] {name} → {verdict}{score_str}")

    full_description = desc + "\n\n" + "\n".join(summary_lines)

    post_github_status_check(repo, sha, state, full_description)

    record = {
        "event_id": delivery_id,
        "repo": repo,
        "pr_number": pr_number,
        "action": action,
        "sender": sender,
        "mcp_identifiers": str(identifiers),
        "verdicts": str(verdicts),
        "status_check_posted": True,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    ws_write("github_webhook_events", [record])

    return {"status": "processed", "verdicts": verdicts, "state": state}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": SERVICE_NAME,
        "endpoints": ["/webhook", "/health"],
        "description": "GitHub PR webhook wiring for ZO-Sentinel MCP safety checks",
    }


def run():
    """Start the webhook service."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_webhook_events_table()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()