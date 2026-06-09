# deps: fastapi requests uvicorn
"""wire_github_pr_checker.py -- ZO-SENTINEL GitHub PR checker wire.

Minimal FastAPI webhook surface on port 8781. It authenticates by requiring
X-GitHub-Event == pull_request, invokes github_pr_checker.check_pr_for_mcps()
for the PR URL, and records the outcome through write_service.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import github_pr_checker

SERVICE_NAME = "wire_github_pr_checker"
SURFACE_NAME = "registry_api"
PORT = 8781
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772")
WEBHOOK_SECRET_ENV = "GITHUB_WEBHOOK_SECRET"
HEARTBEAT_INTERVAL_SECS = 60
REQUEST_TIMEOUT_SECS = 10
MAX_WRITE_RETRIES = 3

log = logging.getLogger(SERVICE_NAME)
app = FastAPI(title="ZO-SENTINEL GitHub PR Checker Wire", version="1.0.0")


def verify_github_signature(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """Verify HMAC-SHA256 signature from GitHub webhook X-Hub-Signature-256 header.
    
    Args:
        payload_bytes: Raw request body bytes
        signature_header: Value of X-Hub-Signature-256 header (format: sha256=<hex>)
    
    Returns:
        True if signature is valid or GITHUB_WEBHOOK_SECRET is not set (dev mode)
    
    Matches the implementation from github_pr_checker_webhook_wiring.py.
    """
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


def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> bool:
    payload = {"table": table, "rows": rows, "wait": wait}
    delay = 0.5
    for attempt in range(MAX_WRITE_RETRIES):
        try:
            response = requests.post(
                f"{WRITE_SERVICE_URL}/write",
                json=payload,
                timeout=REQUEST_TIMEOUT_SECS,
            )
            if response.status_code == 200:
                return True
            log.warning(
                "write_service /write returned %s on attempt %s: %s",
                response.status_code,
                attempt + 1,
                response.text[:200],
            )
        except Exception as exc:
            log.warning("write_service /write failed on attempt %s: %s", attempt + 1, exc)
        if attempt < MAX_WRITE_RETRIES - 1:
            time.sleep(delay)
            delay *= 2
    return False


def record_service_health(status: str, meta: Dict[str, Any]) -> bool:
    return ws_write(
        "service_health",
        [
            {
                "service": SERVICE_NAME,
                "status": status,
                "meta": json.dumps(meta, separators=(",", ":"), sort_keys=True),
            }
        ],
        wait=True,
    )


def record_audit_log(
    *,
    event_id: str,
    repo: str,
    action: str,
    outcome: str,
    details: Dict[str, Any],
) -> bool:
    return ws_write(
        "audit_log",
        [
            {
                "event_id": event_id,
                "event_type": "github_pr_checker_webhook",
                "actor": SERVICE_NAME,
                "target_server_id": repo,
                "action": action,
                "outcome": outcome,
                "details_json": json.dumps(details, separators=(",", ":"), sort_keys=True),
                "immutable": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        wait=True,
    )


def record_audit_log_wrapper(row: Dict[str, Any]) -> bool:
    """Wrapper to accept a single dict (for process_github_pr_webhook)."""
    return record_audit_log(
        event_id=row.get("event_id", ""),
        repo=row.get("repo", ""),
        action=row.get("action", ""),
        outcome=row.get("outcome", ""),
        details=row.get("details", {}),
    )


def build_pr_url(payload: Dict[str, Any]) -> str:
    pr = payload.get("pull_request") or {}
    html_url = pr.get("html_url")
    if html_url:
        return str(html_url)
    repo = (payload.get("repository") or {}).get("full_name")
    number = pr.get("number")
    if repo and number:
        return f"https://github.com/{repo}/pull/{number}"
    return ""


def allowed_pull_request_action(action: str) -> bool:
    return action in {"opened", "synchronize", "reopened"}


def normalize_check_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for result in results:
        item = dict(result)
        verdict = str(item.get("verdict") or "UNKNOWN").upper()
        trust_score = item.get("trust_score")
        if not item.get("risk_tier"):
            if verdict in {"HIGH_RISK", "DANGEROUS"}:
                risk_tier = "high_risk"
            elif verdict == "TRUSTED":
                risk_tier = "trusted"
            elif trust_score is None:
                risk_tier = "unknown"
            elif trust_score >= 0.7:
                risk_tier = "trusted"
            elif trust_score >= 0.4:
                risk_tier = "caution"
            else:
                risk_tier = "high_risk"
            item["risk_tier"] = risk_tier
        item.setdefault("trust_score", trust_score)
        item.setdefault("source", item.get("source") or item.get("name") or "github_pr_checker")
        item.setdefault("reason", item.get("reason") or item.get("verdict_reasoning") or "GitHub PR webhook assessment")
        normalized.append(item)
    return normalized


def process_github_pr_webhook(
    payload: Dict[str, Any],
    event_name: str,
    delivery_id: Optional[str] = None,
    checker: Callable[[str], List[Dict[str, Any]]] = github_pr_checker.check_pr_for_mcps,
    audit_writer: Callable[[Dict[str, Any]], bool] = record_audit_log_wrapper,
) -> Dict[str, Any]:
    if event_name != "pull_request":
        raise HTTPException(status_code=401, detail="X-GitHub-Event must be pull_request")

    action = str(payload.get("action") or "")
    if not allowed_pull_request_action(action):
        return {"status": "ignored", "reason": f"action={action or 'unknown'}", "event": event_name}

    pr_url = build_pr_url(payload)
    if not pr_url:
        raise HTTPException(status_code=400, detail="Missing pull_request html_url or repository/full_name+number")

    repo = (payload.get("repository") or {}).get("full_name") or "unknown"
    event_id = delivery_id or str(uuid.uuid4())

    try:
        results = normalize_check_results(checker(pr_url))
        comment = github_pr_checker.generate_pr_comment(results, pr_url)
        server_ids = [str(result.get("server_id") or result.get("name") or "") for result in results]
        risk_tiers = [str(result.get("risk_tier") or "unknown") for result in results]
        details = {
            "delivery_id": event_id,
            "event": event_name,
            "action": action,
            "repo": repo,
            "pr_url": pr_url,
            "result_count": len(results),
            "server_ids": [sid for sid in server_ids if sid],
            "risk_tiers": risk_tiers,
            "comment": comment,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        audit_writer(
            {
                "event_id": event_id,
                "repo": repo,
                "action": action,
                "outcome": "processed",
                "details": details,
            }
        )
        return {
            "status": "processed",
            "event_id": event_id,
            "repo": repo,
            "pr_url": pr_url,
            "result_count": len(results),
            "results": results,
        }
    except Exception as exc:
        details = {
            "delivery_id": event_id,
            "event": event_name,
            "action": action,
            "repo": repo,
            "pr_url": pr_url,
            "error": str(exc),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        audit_writer(
            {
                "event_id": event_id,
                "repo": repo,
                "action": action,
                "outcome": "error",
                "details": details,
            }
        )
        return {"status": "error", "event_id": event_id, "repo": repo, "pr_url": pr_url, "error": str(exc)}


def handle_github_webhook(
    event_name: str,
    payload: Dict[str, Any],
    delivery_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Handle GitHub webhook for PR safety assessment.
    
    This is the main entry point called by registry_api.py.
    
    Args:
        event_name: X-GitHub-Event header value
        payload: Parsed JSON payload
        delivery_id: X-GitHub-Delivery header value (optional)
    
    Returns:
        Result dict with status and assessment details
    """
    return process_github_pr_webhook(payload, event_name, delivery_id)


# ── FastAPI endpoints (for standalone daemon mode) ─────────────────────────────


@app.post("/webhook/github/pr")
async def github_pr_webhook(request: Request) -> Dict[str, Any]:
    """Standalone webhook endpoint (if running as separate daemon)."""
    event_name = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "") or str(uuid.uuid4())
    if event_name != "pull_request":
        raise HTTPException(status_code=401, detail="X-GitHub-Event must be pull_request")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")
    return await asyncio.to_thread(
        handle_github_webhook,
        event_name,
        payload,
        delivery_id,
    )


@app.post("/v1/github/webhook")
async def github_webhook_alias(request: Request) -> Dict[str, Any]:
    """Alias for /webhook/github/pr (matches registry_api endpoint path)."""
    return await github_pr_webhook(request)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": SERVICE_NAME, "surface": SURFACE_NAME, "port": PORT}


@app.get("/")
def root() -> Dict[str, Any]:
    return {"service": SERVICE_NAME, "surface": SURFACE_NAME, "port": PORT, "routes": ["/webhook/github/pr", "/v1/github/webhook", "/health"]}


def heartbeat_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(HEARTBEAT_INTERVAL_SECS):
        record_service_health(
            "ok",
            {
                "port": PORT,
                "surface": SURFACE_NAME,
                "route": "/webhook/github/pr",
                "heartbeat": datetime.now(timezone.utc).isoformat(),
            },
        )


def run_server() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    record_service_health(
        "starting",
        {"port": PORT, "surface": SURFACE_NAME, "route": "/webhook/github/pr", "started_at": datetime.now(timezone.utc).isoformat()},
    )
    stop_event = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(stop_event,), daemon=True).start()
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", access_log=False)
    finally:
        stop_event.set()
        record_service_health(
            "stopped",
            {"port": PORT, "surface": SURFACE_NAME, "stopped_at": datetime.now(timezone.utc).isoformat()},
        )


def self_smoke() -> None:
    def fake_checker(_: str) -> List[Dict[str, Any]]:
        return [
            {
                "name": "mcp-safe",
                "server_id": "srv-smoke-001",
                "source": "package.json",
                "verdict": "TRUSTED",
                "trust_score": 0.95,
                "risk_tier": "trusted",
            }
        ]

    audit_rows: List[Dict[str, Any]] = []

    def capture_audit(row: Dict[str, Any]) -> bool:
        audit_rows.append(row)
        return True

    payloads = [
        {"action": "opened", "pull_request": {"html_url": "https://github.com/acme/tooling/pull/12", "number": 12}, "repository": {"full_name": "acme/tooling"}},
        {"action": "synchronize", "pull_request": {"number": 7}, "repository": {"full_name": "acme/platform"}},
        {"action": "reopened", "pull_request": {"html_url": "https://github.com/org/repo/pull/99", "number": 99}, "repository": {"full_name": "org/repo"}},
    ]

    results = [process_github_pr_webhook(payload, "pull_request", f"delivery-{idx}", fake_checker, capture_audit) for idx, payload in enumerate(payloads, start=1)]

    assert build_pr_url(payloads[0]) == "https://github.com/acme/tooling/pull/12"
    assert build_pr_url(payloads[1]) == "https://github.com/acme/platform/pull/7"
    assert build_pr_url(payloads[2]) == "https://github.com/org/repo/pull/99"
    assert all(result["status"] == "processed" for result in results)
    assert all(result["result_count"] == 1 for result in results)
    assert len(audit_rows) == 3
    assert all(row["outcome"] == "processed" for row in audit_rows)
    assert audit_rows[0]["details"]["server_ids"] == ["srv-smoke-001"]
    assert audit_rows[0]["details"]["risk_tiers"] == ["trusted"]


def run() -> int:
    self_smoke()
    if os.environ.get("WIRE_GITHUB_PR_CHECKER_RUN_SERVER", "") == "1":
        run_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
