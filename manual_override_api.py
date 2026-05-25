#!/usr/bin/env python3
"""
ZO-SENTINEL: Manual Override API
Port: 8776
"""
import time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Body
import uvicorn
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "manual_override_api"

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

def log_audit(server_id: str, event_type: str, actor: str, detail: str):
    """Log an audit event for manual override actions."""
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "audit_log",
            "rows": {
                "target_server_id": server_id,
                "event_type": event_type,
                "actor": actor,
                "detail": detail,
                "created_at": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=5)
    except Exception:
        pass

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.post("/override/trust")
def override_trust(server_id: str = Body(...), new_score: int = Body(...), actor: str = Body(...)):
    """Manually override trust score for a server."""
    if not 0 <= new_score <= 100:
        raise HTTPException(status_code=400, detail="Trust score must be between 0 and 100")
    
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "mcp_server_registry",
            "rows": {
                "server_id": server_id,
                "trust_score": new_score
            },
            "wait": True
        }, timeout=5)
        
        log_audit(server_id, "trust_override", actor, f"Trust score changed to {new_score}")
        return {"ok": True, "server_id": server_id, "new_score": new_score}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/override/verdict")
def override_verdict(server_id: str = Body(...), new_verdict: str = Body(...), actor: str = Body(...)):
    """Manually override verdict for a server."""
    valid_ verdicts = ["trusted", "suspicious", "malicious", "unknown"]
    if new_verdict not in valid_verdicts:
        raise HTTPException(status_code=400, detail=f"Verdict must be one of: {valid_verdicts}")
    
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "mcp_server_registry",
            "rows": {
                "server_id": server_id,
                "verdict": new_verdict
            },
            "wait": True
        }, timeout=5)
        
        log_audit(server_id, "verdict_override", actor, f"Verdict changed to {new_verdict}")
        return {"ok": True, "server_id": server_id, "new_verdict": new_verdict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run():
    send_heartbeat()
    uvicorn.run(app, host="127.0.0.1", port=8776)

if __name__ == "__main__":
    run()
