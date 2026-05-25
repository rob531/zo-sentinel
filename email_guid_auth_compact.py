#!/usr/bin/env python3
"""ZO-SENTINEL: Email GUID Auth Compact Service - Port 8775"""
from datetime import datetime, timedelta
from fastapi import FastAPI
import uvicorn, requests, hashlib, uuid, time

WRITE = "http://127.0.0.1:8772"
app = FastAPI()

def hb():
    try:
        requests.post(f"{WRITE}/write", json={"table": "service_health", "rows": {"service": "email_guid_auth", "last_heartbeat": datetime.utcnow().isoformat()}, "wait": True}, timeout=5)
    except: pass

@app.get("/health")
async def health(): return {"status": "ok", "service": "email_guid_auth"}

@app.post("/auth/initiate")
async def initiate(email: str):
    token_id = hashlib.sha256(f"{email}:{uuid.uuid4()}:{time.time()}".encode()).hexdigest()[:32]
    expires = (datetime.utcnow() + timedelta(hours=72)).isoformat()
    requests.post(f"{WRITE}/write", json={"table": "auth_tokens", "rows": {"token_id": token_id, "action": "initiated", "admin_email": email, "expires_at": expires, "used": False}, "wait": True}, timeout=5)
    return {"token_id": token_id, "expires_at": expires}

@app.post("/auth/verify")
async def verify(token_id: str):
    r = requests.post(f"{WRITE}/query", json={"sql": f"SELECT * FROM auth_tokens WHERE token_id = '{token_id}' AND used = false"}, timeout=5).json()
    if not r.get("rows"): return {"error": "not found"}
    row = r["rows"][0]
    if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]): return {"error": "expired"}
    requests.post(f"{WRITE}/write", json={"table": "auth_tokens", "rows": {"token_id": token_id, "used": True, "used_at": datetime.utcnow().isoformat()}, "wait": True}, timeout=5)
    return {"verified": True, "email": row["admin_email"]}

if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=8775)
