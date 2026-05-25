#!/usr/bin/env python3
"""
ZO-SENTINEL: Rebuild Failed Build Scripts
Generates fresh build scripts for previously failed modules.
"""

import os
import stat

OUTPUT_DIR = "/home/workspace/zo_sentinel"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SCRIPTS = {}

# 1. start_all.sh - Master startup script
SCRIPTS['start_all.sh'] = '''#!/bin/bash
# ZO-SENTINEL: Master Startup Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== ZO-SENTINEL Startup ==="

# Start all services
python3 "$SCRIPT_DIR/email_guid_auth.py" &
python3 "$SCRIPT_DIR/advanced_filter_api.py" &
python3 "$SCRIPT_DIR/forensic_detail_api.py" &
python3 "$SCRIPT_DIR/manual_override_api.py" &
python3 "$SCRIPT_DIR/supervisor_auto_updater.py" &

# Start UI server
python3 "$SCRIPT_DIR/ui_server.py" &

echo "All services launched. Use 'bash start_all.sh stop' to halt."
wait
'''

# 2. email_guid_auth.py (port 8775)
SCRIPTS['email_guid_auth.py'] = '''#!/usr/bin/env python3
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
'''

# 3. advanced_filter_api.py (port 8777)
SCRIPTS['advanced_filter_api.py'] = '''#!/usr/bin/env python3
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
'''

# 4. forensic_detail_api.py (port 8779)
SCRIPTS['forensic_detail_api.py'] = '''#!/usr/bin/env python3
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
'''

# 5. manual_override_api.py (port 8776)
SCRIPTS['manual_override_api.py'] = '''#!/usr/bin/env python3
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
'''

# 6. compliance_export_service.py
SCRIPTS['compliance_export_service.py'] = '''#!/usr/bin/env python3
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
'''

# 7. supervisor_auto_updater.py (daemon)
SCRIPTS['supervisor_auto_updater.py'] = '''#!/usr/bin/env python3
"""
ZO-SENTINEL: Supervisor Auto-Updater Daemon
Checks service health and restarts unhealthy services.
"""
import os
import time
import signal
import subprocess
from datetime import datetime
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "supervisor_auto_updater"
HEARTBEAT_INTERVAL = 30
HEALTH_CHECK_INTERVAL = 60
STALE_THRESHOLD_SECONDS = 180

PID_DIR = "/tmp"
SERVICE_SCRIPTS = {
    "email_guid_auth": "/home/workspace/zo_sentinel/email_guid_auth.py",
    "advanced_filter_api": "/home/workspace/zo_sentinel/advanced_filter_api.py",
    "forensic_detail_api": "/home/workspace/zo_sentinel/forensic_detail_api.py",
    "manual_override_api": "/home/workspace/zo_sentinel/manual_override_api.py",
}

running = True

def check_single_instance():
    """Ensure only one instance of this daemon runs."""
    pid_file = f"{PID_DIR}/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Another instance (PID {old_pid}) is running. Exiting.")
            exit(1)
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

def cleanup_handler(signum, frame):
    global running
    running = False
    pid_file = f"{PID_DIR}/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        os.remove(pid_file)
    exit(0)

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

def get_unhealthy_services():
    """Query write_service for stale services."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": "SELECT service, last_heartbeat FROM service_health"
        }, timeout=10)
        data = resp.json()
        unhealthy = []
        
        for row in data.get("rows", []):
            last_hb = datetime.fromisoformat(row["last_heartbeat"])
            age = (datetime.utcnow() - last_hb).total_seconds()
            if age > STALE_THRESHOLD_SECONDS:
                unhealthy.append(row["service"])
        
        return unhealthy
    except Exception:
        return []

def restart_service(service_name: str):
    """Restart an unhealthy service."""
    if service_name not in SERVICE_SCRIPTS:
        return False
    
    script = SERVICE_SCRIPTS[service_name]
    if not os.path.exists(script):
        return False
    
    try:
        subprocess.Popen(["python3", script])
        return True
    except Exception:
        return False

def run():
    check_single_instance()
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    
    print(f"{SERVICE_NAME} started (PID {os.getpid()})")
    
    last_health_check = 0
    
    while running:
        now = time.time()
        
        send_heartbeat()
        
        if now - last_health_check >= HEALTH_CHECK_INTERVAL:
            unhealthy = get_unhealthy_services()
            for svc in unhealthy:
                print(f"Restarting unhealthy service: {svc}")
                if restart_service(svc):
                    print(f"  -> {svc} restarted successfully")
                else:
                    print(f"  -> Failed to restart {svc}")
            last_health_check = now
        
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == "__main__":
    run()
'''

# 8. email_guid_auth_compact.py
SCRIPTS['email_guid_auth_compact.py'] = '''#!/usr/bin/env python3
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
'''

# 9. mcp_detail_view_ui.py
SCRIPTS['mcp_detail_view_ui.py'] = '''#!/usr/bin/env python3
"""ZO-SENTINEL: MCP Detail View UI - Port 8790"""
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
import uvicorn, requests

WRITE = "http://127.0.0.1:8772"
app = FastAPI()

def hb():
    try:
        requests.post(f"{WRITE}/write", json={"table": "service_health", "rows": {"service": "mcp_detail_view_ui", "last_heartbeat": datetime.utcnow().isoformat()}, "wait": True}, timeout=5)
    except: pass

@app.get("/health")
async def health(): return {"status": "ok", "service": "mcp_detail_view_ui"}

@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!DOCTYPE html>
<html>
<head><title>MCP Detail View - ZO-SENTINEL</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
</head>
<body>
<div class="container mt-4">
<h1>MCP Detail Viewer</h1>
<form onsubmit="event.preventDefault(); fetchDetail(document.getElementById('serverId').value);">
<div class="input-group mb-3">
<input type="text" id="serverId" class="form-control" placeholder="Enter Server ID" required>
<button class="btn btn-primary" type="submit">Fetch Detail</button>
</div>
</form>
<div id="result" class="mt-4"></div>
</div>
<script>
async function fetchDetail(id) {
    const resp = await fetch('http://127.0.0.1:8779/server/' + id);
    const data = await resp.json();
    document.getElementById('result').innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
}
</script>
</body></html>"""

@app.get("/server/{server_id}")
async def get_server(server_id: str):
    try:
        resp = requests.post(f"{WRITE}/query", json={"sql": f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'"}, timeout=10)
        rows = resp.json().get("rows", [])
        if not rows: raise HTTPException(status_code=404, detail="Server not found")
        return rows[0]
    except: raise HTTPException(status_code=500, detail="Service unavailable")

if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=8790)
'''

# Now write all scripts to disk
for filename, content in SCRIPTS.items():
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w') as f:
        f.write(content)
    
    if filename.endswith('.sh'):
        os.chmod(filepath, os.stat(filepath).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    
    print(f"Generated: {filepath}")

print(f"\n=== Rebuild Complete: {len(SCRIPTS)} scripts generated ===")