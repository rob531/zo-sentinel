#!/usr/bin/env python3
"""
ZO-SENTINEL: Advanced Filter API
Port: 8777
"""
import time
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
import uvicorn
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "advanced_filter_api"

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

@app.post("/filter/servers")
def filter_servers(filters: dict):
    """Apply advanced filters to MCP server registry."""
    trust_min = filters.get("trust_score_min", 0)
    trust_max = filters.get("trust_score_max", 100)
    verdict = filters.get("verdict")
    sources = filters.get("registry_sources", [])
    threat_types = filters.get("threat_types", [])
    
    conditions = [
        f"trust_score >= {trust_min}",
        f"trust_score <= {trust_max}"
    ]
    
    if verdict:
        conditions.append(f"verdict = '{verdict}'")
    
    if sources:
        source_list = "', '".join(sources)
        conditions.append(f"registry_source IN ('{source_list}')")
    
    where_clause = " AND ".join(conditions)
    
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_server_registry WHERE {where_clause}"
        }, timeout=10)
        results = resp.json()
        
        if threat_types:
            filtered = []
            for row in results.get("rows", []):
                server_id = row.get("server_id")
                threat_resp = requests.post(f"{WRITE_SERVICE}/query", json={
                    "sql": f"SELECT threat_type FROM mcp_threat_associations WHERE server_id = '{server_id}'"
                }, timeout=5)
                server_threats = [t["threat_type"] for t in threat_resp.json().get("rows", [])]
                if any(tt in threat_types for tt in server_threats):
                    row["matched_threats"] = server_threats
                    filtered.append(row)
            results["rows"] = filtered
        
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/filter/risks")
def filter_risks(criteria: dict):
    """Query risk register with filters."""
    tier = criteria.get("risk_tier")
    min_rank = criteria.get("min_rank", 0)
    max_rank = criteria.get("max_rank", 999)
    
    where_parts = [f"risk_rank >= {min_rank}", f"risk_rank <= {max_rank}"]
    if tier:
        where_parts.append(f"risk_tier = '{tier}'")
    
    where_clause = " AND ".join(where_parts)
    
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": f"SELECT * FROM mcp_risk_register WHERE {where_clause}"
        }, timeout=10)
        return resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run():
    send_heartbeat()
    uvicorn.run(app, host="127.0.0.1", port=8777)

if __name__ == "__main__":
    run()
