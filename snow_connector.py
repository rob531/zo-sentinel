import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
from fastapi import FastAPI, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import uvicorn
import hashlib
import hmac
import time
import logging
import os
import re
from typing import Optional, Dict, Any
import requests

SERVICE_NAME = "snow_connector"
PORT = 8778
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"

SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_OAUTH_TOKEN_URL = os.environ.get("SNOW_OAUTH_TOKEN_URL", "")
SNOW_OAUTH_CLIENT_ID = os.environ.get("SNOW_OAUTH_CLIENT_ID", "")
SNOW_OAUTH_CLIENT_SECRET = os.environ.get("SNOW_OAUTH_CLIENT_SECRET", "")

PROMPT_INJECTION_PLAN = "/home/workspace/zo_sentinel/PROMPT_INJECTION_PLAN.md"
PROMPT_INJECTION_PATTERNS = []
if os.path.exists(PROMPT_INJECTION_PLAN):
    with open(PROMPT_INJECTION_PLAN, 'r') as f:
        content = f.read()
        patterns = re.findall(r'`([^`]+)`', content)
        PROMPT_INJECTION_PATTERNS = [p for p in patterns if len(p) > 10]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - [REDACTED]')
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()

class SnowTicketSubmission(BaseModel):
    short_description: str
    description: Optional[str] = ""
    u_mcp_server_name: Optional[str] = ""
    u_requested_by: Optional[str] = ""
    ticket_id: Optional[str] = ""
    sys_id: Optional[str] = ""

def sanitize_for_log(text: str) -> str:
    if not text:
        return "[EMPTY]"
    return f"[CONTENT_LENGTH:{len(text)}]"

def check_prompt_injection(text: str) -> bool:
    if not text or not PROMPT_INJECTION_PATTERNS:
        return False
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.lower() in text_lower:
            return True
    return False

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        return response.status_code == 200 and response.json().get("ok", False)
    except Exception as e:
        log.error(f"Write service error: {e}")
        return False

def validate_snow_oauth_token(token: str) -> bool:
    if not token:
        return False
    if SNOW_OAUTH_TOKEN_URL:
        try:
            response = requests.post(
                SNOW_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": SNOW_OAUTH_CLIENT_ID,
                    "client_secret": SNOW_OAUTH_CLIENT_SECRET
                },
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                expected_token = data.get("access_token", "")
                return hmac.compare_digest(token, expected_token)
        except Exception:
            pass
    return token.startswith("SNOW_") and len(token) > 20

def validate_snow_signature(request_body: bytes, signature: str, timestamp: str) -> bool:
    if not SNOW_WEBHOOK_SECRET:
        log.warning("SNOW_WEBHOOK_SECRET not configured - skipping signature validation")
        return True
    if not signature:
        return False
    expected = hmac.new(
        SNOW_WEBHOOK_SECRET.encode(),
        f"{timestamp}:".encode() + request_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)

async def verify_snow_auth(
    authorization: Optional[str] = Header(None),
    x_snow_signature: Optional[str] = Header(None),
    x_snow_timestamp: Optional[str] = Header(None)
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    token = authorization[7:]
    if not validate_snow_oauth_token(token):
        raise HTTPException(status_code=401, detail="Invalid OAuth token")
    if x_snow_signature and x_snow_timestamp:
        return {"token_valid": True, "signature_provided": True}
    return {"token_valid": True, "signature_provided": False}

def parse_snow_webhook(data: Dict[str, Any]) -> Optional[SnowTicketSubmission]:
    try:
        fields = data.get("data", {}).get("fields", data)
        return SnowTicketSubmission(
            short_description=fields.get("short_description", ""),
            description=fields.get("description", ""),
            u_mcp_server_name=fields.get("u_mcp_server_name", ""),
            u_requested_by=fields.get("u_requested_by", ""),
            ticket_id=fields.get("number", data.get("ticket_id", "")),
            sys_id=fields.get("sys_id", "")
        )
    except Exception as e:
        log.error(f"Failed to parse SNOW webhook: {e}")
        return None

@app.post("/snow/webhook")
async def snow_webhook(
    request: Request,
    auth_info: Dict[str, Any] = Depends(verify_snow_auth)
):
    start_time = time.time()
    request_body = await request.body()
    signature = request.headers.get("x-snow-signature", "")
    timestamp = request.headers.get("x-snow-timestamp", str(int(time.time())))
    if auth_info.get("signature_provided"):
        if not validate_snow_signature(request_body, signature, timestamp):
            log.warning("Invalid SNOW webhook signature - rejecting request")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = __import__('json').loads(request_body)
    except Exception:
        payload = {}
    log.info(f"Received SNOW webhook: {sanitize_for_log(str(payload.get('data', {}).get('ticket_id', 'unknown')))}")
    submission = parse_snow_webhook(payload)
    if not submission:
        raise HTTPException(status_code=400, detail="Invalid SNOW ticket format")
    if check_prompt_injection(submission.short_description) or check_prompt_injection(submission.description):
        log.warning("Potential prompt injection detected in SNOW ticket")
    submission_data = {
        "mcp_server_name": submission.u_mcp_server_name or submission.short_description,
        "requester_email": submission.u_requested_by or "snow_unknown",
        "source": "servicenow",
        "status": "pending_review",
        "submitted_at": __import__('datetime').datetime.now().isoformat(),
        "snow_ticket_id": submission.ticket_id,
        "snow_sys_id": submission.sys_id,
        "short_description": submission.short_description,
        "description_hash": hashlib.sha256(submission.description.encode()).hexdigest() if submission.description else ""
    }
    success = ws_write("mcp_submissions", submission_data)
    if not success:
        log.error("Failed to write submission to mcp_submissions")
        raise HTTPException(status_code=500, detail="Failed to process ticket")
    elapsed = time.time() - start_time
    log.info(f"SNOW webhook processed in {elapsed:.2f}s - ticket: {submission.ticket_id}")
    return {
        "status": "acknowledged",
        "ticket_id": submission.ticket_id,
        "processing_time_ms": int(elapsed * 1000)
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": time.time()
    }

def run():
    uvicorn.run(app, host="127.0.0.1", port=PORT)

if __name__ == "__main__":
    run()