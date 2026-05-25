#!/usr/bin/env python3
"""
ZO-SENTINEL: Forensic Detail API
Port: 8779
"""
import time
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
import uvicorn
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "forensic_detail_api"

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

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.get("/server/{server_id}")
def get_server_detail(server_id: str):
    """Get detailed forensic information for a server."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'"
        }, timeout=10)
        data = resp.json()
        
        if not data.get("rows"):
            raise HTTPException(status_code=404, detail="Server not found")
        
        result = {"server": data["rows"][0]}
        
        signals_resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}'"
        }, timeout=10)
        result["signals"] = signals_resp.json().get("rows", [])
        
        threats_resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_threat_associations WHERE server_id = '{server_id}'"
        }, timeout=10)
        result["threats"] = threats_resp.json().get("rows", [])
        
        risk_resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_risk_register WHERE server_id = '{server_id}'"
        }, timeout=10)
        result["risk"] = risk_resp.json().get("rows", [None])[0]
        
        audit_resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM audit_log WHERE target_server_id = '{server_id}' ORDER BY created_at DESC LIMIT 50"
        }, timeout=10)
        result["audit_trail"] = audit_resp.json().get("rows", [])
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/server/{server_id}/timeline")
def get_server_timeline(server_id: str, days: int = Query(default=30, ge=1, le=365)):
    """Get timeline of events for a server."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM audit_log WHERE target_server_id = '{server_id}' AND created_at >= '{cutoff}' ORDER BY created_at DESC"
        }, timeout=10)
        return {"server_id": server_id, "days": days, "events": resp.json().get("rows", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run():
    send_heartbeat()
    uvicorn.run(app, host="127.0.0.1", port=8779)

if __name__ == "__main__":
    run()
