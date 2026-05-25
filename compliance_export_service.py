#!/usr/bin/env python3
"""
ZO-SENTINEL: Compliance Export Service
"""
import time
import csv
import io
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "compliance_export_service"

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

@app.get("/export/servers")
def export_servers_csv():
    """Export all servers as CSV."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": "SELECT * FROM mcp_server_registry"
        }, timeout=30)
        data = resp.json()
        
        output = io.StringIO()
        if data.get("rows"):
            writer = csv.DictWriter(output, fieldnames=data["rows"][0].keys())
            writer.writeheader()
            writer.writerows(data["rows"])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=servers_export.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export/audit")
def export_audit_csv(since: Optional[str] = None):
    """Export audit log as CSV."""
    sql = "SELECT * FROM audit_log"
    if since:
        sql += f" WHERE created_at >= '{since}'"
    sql += " ORDER BY created_at DESC"
    
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": sql
        }, timeout=30)
        data = resp.json()
        
        output = io.StringIO()
        if data.get("rows"):
            writer = csv.DictWriter(output, fieldnames=data["rows"][0].keys())
            writer.writeheader()
            writer.writerows(data["rows"])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_export.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run():
    send_heartbeat()
    uvicorn.run(app, host="127.0.0.1", port=8782)

if __name__ == "__main__":
    run()
