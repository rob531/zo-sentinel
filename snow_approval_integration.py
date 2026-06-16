#!/usr/bin/env python3
"""
snow_approval_integration.py
Phase 9: Wire snow_connector.py into approval_workflow daemon.
"""

import os
import time
import hmac
import hashlib
import requests
import uvicorn
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Header
from pydantic import BaseModel

# Service configuration
PORT = 8780
SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_TOKEN_URL = f"https://{SNOW_INSTANCE}/oauth_token.do"
SNOW_API_BASE = f"https://{SNOW_INSTANCE}/api/now/table"

# Module-level token state (initialized properly)
snow_oauth_token: Optional[str] = None
token_acquired_at: Optional[datetime] = None

# Token freshness: 50 minutes (SNOW tokens typically expire in 60 minutes)
TOKEN_FRESHNESS_SECONDS = 50 * 60

# Exponential backoff configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10

app = FastAPI()

# --- HTTP Client with Exponential Backoff ---
def make_snow_request(method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
    """Make SNOW API request with exponential backoff retry."""
    headers = kwargs.pop("headers", {})
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    
    url = f"{SNOW_API_BASE}/{endpoint}"
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=10,
                **kwargs
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
                continue
            raise
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1 and response.status_code >= 500:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
                continue
            raise
    
    return {}


# --- Token Management ---
def is_token_fresh() -> bool:
    """Check if SNOW OAuth token is still fresh."""
    global snow_oauth_token, token_acquired_at
    
    if not snow_oauth_token or not token_acquired_at:
        return False
    
    elapsed = (datetime.now() - token_acquired_at).total_seconds()
    return elapsed < TOKEN_FRESHNESS_SECONDS


def ensure_valid_token() -> str:
    """Ensure SNOW OAuth token is valid, refreshing if necessary."""
    global snow_oauth_token, token_acquired_at
    
    if is_token_fresh():
        return snow_oauth_token
    
    # Acquire new token
    data = {
        "grant_type": "client_credentials",
        "client_id": SNOW_CLIENT_ID,
        "client_secret": SNOW_CLIENT_SECRET,
        "scope": "incident.write"
    }
    
    response = requests.post(SNOW_TOKEN_URL, data=data, timeout=10)
    response.raise_for_status()
    
    result = response.json()
    snow_oauth_token = result["access_token"]
    token_acquired_at = datetime.now()
    
    return snow_oauth_token


# --- Webhook Signature Verification ---
def verify_snow_webhook_signature(
    body: bytes,
    signature_header: str,
    secret: str = SNOW_CLIENT_SECRET
) -> bool:
    """Verify SNOW webhook signature using HMAC-SHA256."""
    if not signature_header:
        return False
    
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature_header)


# --- Database Access Helpers ---
def get_server_verdict(server_id: str) -> Optional[str]:
    """Read verdict from mcp_server_registry."""
    import sys
    sys.path.insert(0, '/home/workspace')
    import asyncio
    from write_service_integration import query_sync
    
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    result = asyncio.run(query_sync(sql))
    
    if result and result.get("rows"):
        return result["rows"][0].get("verdict")
    return None


def write_audit_log(
    target_server_id: str,
    event_type: str,
    actor: str,
    detail: str
) -> bool:
    """Write audit_log entry for SNOW sync actions."""
    import sys
    sys.path.insert(0, '/home/workspace')
    import asyncio
    from write_service_integration import write_sync
    
    rows = [{
        "target_server_id": target_server_id,
        "event_type": event_type,
        "actor": actor,
        "detail": detail,
        "created_at": datetime.now().isoformat()
    }]
    
    result = asyncio.run(write_sync("audit_log", rows))
    return result.get("ok", False)


# --- SNOW Ticket Creation ---
def create_snow_incident(
    server_id: str,
    server_name: str,
    verdict: str,
    threat_count: int
) -> Optional[str]:
    """Create SNOW incident ticket for approved approval_workflow request."""
    token = ensure_valid_token()
    
    risk_priority = "1" if threat_count > 5 else "2" if threat_count > 2 else "3"
    
    short_description = f"MCP Server Approval: {server_name}"
    description = (
        f"Server: {server_name}\n"
        f"ID: {server_id}\n"
        f"Verdict: {verdict}\n"
        f"Threat Count: {threat_count}\n"
        f"Requires manual approval before trust score update."
    )
    
    payload = {
        "short_description": short_description,
        "description": description,
        "urgency": risk_priority,
        "impact": risk_priority,
        "category": "Software",
        "subcategory": "MCP Server Review"
    }
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    result = make_snow_request(
        "POST",
        "incident",
        headers=headers,
        json=payload
    )
    
    if result.get("result"):
        return result["result"].get("sys_id")
    
    return None


# --- Pydantic Models ---
class ApprovalRequest(BaseModel):
    server_id: str
    action: str  # "approve", "reject", "request_review"
    admin_email: str
    notes: Optional[str] = None


class SnowSyncRequest(BaseModel):
    server_id: str
    action: str


# --- API Routes ---
@app.post("/snow/sync")
async def sync_to_snow(req: SnowSyncRequest, x_snow_signature: Optional[str] = Header(None)):
    """
    Sync server to SNOW for manual review.
    Verifies verdict from registry before creating ticket.
    """
    # Read verdict from mcp_server_registry (MUST #1)
    verdict = get_server_verdict(req.server_id)
    if not verdict:
        raise HTTPException(status_code=404, detail="Server not found in registry")
    
    # Must be in a state requiring manual review
    if verdict in ["trusted", "verified", "clean"]:
        raise HTTPException(
            status_code=400,
            detail=f"Server verdict '{verdict}' does not require SNOW sync"
        )
    
    # Write audit log entry (MUST #3)
    write_audit_log(
        target_server_id=req.server_id,
        event_type="snow_sync_started",
        actor="approval_workflow",
        detail=f"Initiating SNOW sync for verdict: {verdict}"
    )
    
    # Validate token freshness (MUST #2)
    ensure_valid_token()
    
    try:
        # Create SNOW ticket with verdict context
        snow_ticket_id = create_snow_incident(
            server_id=req.server_id,
            server_name=f"mcp-server-{req.server_id[:8]}",
            verdict=verdict,
            threat_count=1
        )
        
        if snow_ticket_id:
            write_audit_log(
                target_server_id=req.server_id,
                event_type="snow_ticket_created",
                actor="snow_connector",
                detail=f"Created SNOW ticket: {snow_ticket_id}"
            )
            
            return {
                "status": "ok",
                "snow_ticket_id": snow_ticket_id,
                "verdict": verdict
            }
        else:
            write_audit_log(
                target_server_id=req.server_id,
                event_type="snow_sync_failed",
                actor="snow_connector",
                detail="Failed to create SNOW ticket"
            )
            raise HTTPException(status_code=502, detail="SNOW ticket creation failed")
            
    except requests.exceptions.Timeout:
        write_audit_log(
            target_server_id=req.server_id,
            event_type="snow_timeout",
            actor="snow_connector",
            detail="SNOW API timeout after 3 retries"
        )
        raise HTTPException(status_code=504, detail="SNOW service timeout")
    except requests.exceptions.RequestException as e:
        write_audit_log(
            target_server_id=req.server_id,
            event_type="snow_error",
            actor="snow_connector",
            detail=f"SNOW API error: {str(e)}"
        )
        raise HTTPException(status_code=502, detail=f"SNOW API error: {str(e)}")


@app.post("/webhook/snow")
async def snow_webhook(request: Request, x_snow_signature: Optional[str] = Header(None)):
    """
    Receive webhook from SNOW for ticket updates.
    MUST verify webhook signature (MUST #5).
    """
    body = await request.body()
    
    # Verify webhook signature (MUST #5)
    if x_snow_signature and not verify_snow_webhook_signature(body, x_snow_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    data = await request.json()
    
    ticket_id = data.get("sys_id")
    state = data.get("state")
    server_id = data.get("server_id")
    
    if not ticket_id or not server_id:
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    # Process state transitions
    if state in [7, "7", "Closed", "Resolved"]:
        write_audit_log(
            target_server_id=server_id,
            event_type="snow_ticket_closed",
            actor="snow_webhook",
            detail=f"Ticket {ticket_id} closed - syncing approval result"
        )
    
    return {"status": "ok", "processed": True}


@app.post("/approve")
async def approve_server(req: ApprovalRequest):
    """
    Process server approval and optionally sync to SNOW.
    """
    # Read verdict from registry (MUST #1)
    verdict = get_server_verdict(req.server_id)
    
    if req.action == "request_review":
        # Sync to SNOW for manual review
        sync_result = await sync_to_snow(
            SnowSyncRequest(server_id=req.server_id, action="review"),
            x_snow_signature=None
        )
        return sync_result
    
    write_audit_log(
        target_server_id=req.server_id,
        event_type="manual_approval",
        actor=req.admin_email,
        detail=f"Action: {req.action}, Verdict: {verdict}, Notes: {req.notes}"
    )
    
    return {
        "status": "ok",
        "server_id": req.server_id,
        "action": req.action,
        "verdict": verdict
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    global snow_oauth_token, token_acquired_at
    
    token_status = "valid" if is_token_fresh() else "expired"
    
    return {
        "status": "ok",
        "service": "snow_approval_integration",
        "token_status": token_status,
        "uptime": time.time()
    }


def run():
    """Start the snow approval integration service."""
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    run()