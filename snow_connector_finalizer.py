#!/usr/bin/env python3
"""
snow_connector_finalizer.py
Integration wiring for snow_connector into approval_workflow
Wires: ServiceNow inbound webhook, verdict validation, mcp_decisions write
"""
import sys
import os
sys.path.insert(0, '/home/workspace/zo_sentinel')

import asyncio
import time
import json
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Header
from typing import Optional
import uvicorn

SERVICE_NAME = "snow_connector_finalizer"
SERVICE_PORT = 8780
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

POLL_SECS = 30
app = FastAPI()

start_time = time.time()


def check_single_instance():
    """Ensure only one instance runs via PID file."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            print(f"[FATAL] Service already running as PID {existing_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))


def send_heartbeat():
    """Send heartbeat to service_health via write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        if resp.status_code != 200:
            print(f"[WARN] Heartbeat failed: {resp.status_code}")
    except Exception as e:
        print(f"[WARN] Heartbeat error: {e}")


def write_to_db(table: str, rows: dict) -> bool:
    """Write record to DB via write_service."""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        print(f"[ERROR] DB write failed: {resp.text}")
        return False
    except Exception as e:
        print(f"[ERROR] DB write exception: {e}")
        return False


def query_db(sql: str) -> list:
    """Query DB via query_service. Returns list of rows."""
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        print(f"[ERROR] DB query failed: {resp.text}")
        return []
    except Exception as e:
        print(f"[ERROR] DB query exception: {e}")
        return []


def get_server_verdict(server_id: str) -> Optional[str]:
    """Query mcp_signal_scores for server verdict."""
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = query_db(sql)
    if rows and len(rows) > 0:
        return rows[0].get("verdict")
    return None


def get_latest_signal_score(server_id: str) -> Optional[dict]:
    """Get the most recent signal score for a server."""
    sql = f"""
    SELECT signal_name, score, evidence, scored_at 
    FROM mcp_signal_scores 
    WHERE server_id = '{server_id}'
    ORDER BY scored_at DESC 
    LIMIT 1
    """
    rows = query_db(sql)
    if rows:
        return rows[0]
    return None


def check_risk_tier(server_id: str) -> Optional[str]:
    """Get risk tier from mcp_risk_register."""
    sql = f"SELECT risk_tier FROM mcp_risk_register WHERE server_id = '{server_id}'"
    rows = query_db(sql)
    if rows:
        return rows[0].get("risk_tier")
    return None


def validate_approval_eligibility(server_id: str) -> dict:
    """Validate verdict and risk check before approval."""
    verdict = get_server_verdict(server_id)
    risk_tier = check_risk_tier(server_id)
    latest_signal = get_latest_signal_score(server_id)
    
    result = {
        "eligible": False,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "signal_score": None,
        "reasons": []
    }
    
    if not verdict:
        result["reasons"].append("No verdict found for server")
    elif verdict.lower() == "malicious":
        result["reasons"].append(f"Server has malicious verdict: {verdict}")
    elif verdict.lower() in ["suspicious", "unknown"]:
        result["reasons"].append(f"Server has non-approvable verdict: {verdict}")
    else:
        result["eligible"] = True
    
    if risk_tier and risk_tier.lower() in ["critical", "high"]:
        result["eligible"] = False
        result["reasons"].append(f"Server risk tier is {risk_tier} - requires manual review")
    
    if latest_signal:
        result["signal_score"] = latest_signal.get("score")
        if latest_signal.get("score", 0) < 30:
            result["eligible"] = False
            result["reasons"].append(f"Signal score too low: {latest_signal.get('score')}")
    
    return result


def write_mcp_decision(server_id: str, decision: str, snow_ticket: str, 
                       approval_eligible: dict, approved_by: str = "snow_webhook") -> bool:
    """Write approval decision to mcp_decisions via write_service."""
    decision_record = {
        "server_id": server_id,
        "decision": decision,
        "snow_ticket_id": snow_ticket,
        "decision_timestamp": datetime.utcnow().isoformat(),
        "eligibility_check": json.dumps(approval_eligible),
        "decided_by": approved_by
    }
    return write_to_db("mcp_decisions", decision_record)


@app.get("/health")
def health():
    """Health check endpoint."""
    uptime = int(time.time() - start_time)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": uptime
    }


@app.post("/webhook/snow/inbound")
async def snow_inbound_webhook(request: Request):
    """
    ServiceNow inbound webhook handler.
    Receives SNOW ticket approval/cancellation events.
    """
    try:
        body = await request.json()
        print(f"[INFO] Received SNOW webhook: {json.dumps(body)[:500]}")
        
        event_type = body.get("event_type", body.get("u_event_type", ""))
        snow_ticket_id = body.get("ticket_id", body.get("sys_id", ""))
        server_id = body.get("server_id", body.get("mcp_server_id", ""))
        requestor = body.get("requestor", body.get("u_requestor", "unknown"))
        comments = body.get("comments", body.get("u_comments", ""))
        
        if not server_id:
            raise HTTPException(status_code=400, detail="Missing server_id in webhook")
        
        if event_type == "approval_request":
            validation = validate_approval_eligibility(server_id)
            
            decision = "auto_approved" if validation["eligible"] else "auto_rejected"
            write_mcp_decision(server_id, decision, snow_ticket_id, validation, "snow_auto_check")
            
            return {
                "status": "processed",
                "ticket_id": snow_ticket_id,
                "server_id": server_id,
                "eligible": validation["eligible"],
                "decision": decision,
                "validation": validation
            }
        
        elif event_type == "manual_approval" or event_type == "approved":
            validation = validate_approval_eligibility(server_id)
            
            if validation["eligible"] or requestor == "admin_override":
                write_mcp_decision(server_id, "approved", snow_ticket_id, validation, requestor)
                return {"status": "approved", "ticket_id": snow_ticket_id, "server_id": server_id}
            else:
                write_mcp_decision(server_id, "rejected", snow_ticket_id, validation, requestor)
                return {"status": "rejected", "ticket_id": snow_ticket_id, "validation": validation}
        
        elif event_type == "rejected" or event_type == "cancelled":
            write_mcp_decision(server_id, "rejected", snow_ticket_id, {"manual": True}, requestor)
            return {"status": "recorded", "ticket_id": snow_ticket_id, "decision": "rejected"}
        
        else:
            print(f"[WARN] Unknown SNOW event type: {event_type}")
            return {"status": "ignored", "reason": f"Unknown event type: {event_type}"}
            
    except Exception as e:
        print(f"[ERROR] Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/approve/{server_id}")
def manual_approve(server_id: str, requestor: str = "unknown"):
    """Manual approval endpoint with verdict validation."""
    validation = validate_approval_eligibility(server_id)
    
    if validation["eligible"]:
        write_mcp_decision(server_id, "approved", f"manual_{int(time.time())}", validation, requestor)
        return {"status": "approved", "server_id": server_id, "validation": validation}
    else:
        write_mcp_decision(server_id, "rejected", f"manual_{int(time.time())}", validation, requestor)
        return {"status": "rejected", "server_id": server_id, "validation": validation}


@app.get("/eligibility/{server_id}")
def check_eligibility(server_id: str):
    """Check approval eligibility for a server."""
    validation = validate_approval_eligibility(server_id)
    return {"server_id": server_id, "validation": validation}


def run():
    """Main daemon loop."""
    check_single_instance()
    print(f"[INFO] Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            print(f"[ERROR] Heartbeat cycle error: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")