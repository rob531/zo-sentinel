#!/usr/bin/env python3
"""
ZO-SENTINEL: Email GUID Authentication Service
Port: 8775
"""
import time
import uuid
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
import uvicorn
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "email_guid_auth"
TOKEN_TTL_HOURS = 72

app = FastAPI()

def send_heartbeat():
    """Send service heartbeat to write_service."""
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=5)
    except Exception:
        pass

def generate_guid_token(email: str) -> str:
    """Generate a GUID-based token for email authentication."""
    raw = f"{email}:{uuid.uuid4()}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.post("/auth/initiate")
def initiate_auth(email: str):
    """Initiate authentication for an email address."""
    token_id = generate_guid_token(email)
    expires_at = (datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)).isoformat()
    
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "auth_tokens",
            "rows": {
                "token_id": token_id,
                "action": "initiated",
                "mcp_name": None,
                "submission_id": None,
                "admin_email": email,
                "expires_at": expires_at,
                "used": False,
                "used_at": None
            },
            "wait": True
        }, timeout=5)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"token_id": token_id, "expires_at": expires_at, "email": email}

@app.post("/auth/verify")
def verify_token(token_id: str):
    """Verify and consume an auth token."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM auth_tokens WHERE token_id = '{token_id}' AND used = false"
        }, timeout=5)
        data = resp.json()
        
        if not data.get("rows"):
            raise HTTPException(status_code=404, detail="Token not found or already used")
        
        row = data["rows"][0]
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires:
            raise HTTPException(status_code=410, detail="Token expired")
        
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "auth_tokens",
            "rows": {
                "token_id": token_id,
                "used": True,
                "used_at": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=5)
        
        return {"verified": True, "admin_email": row["admin_email"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run():
    send_heartbeat()
    uvicorn.run(app, host="127.0.0.1", port=8775)

if __name__ == "__main__":
    run()
