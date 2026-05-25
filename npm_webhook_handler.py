#!/usr/bin/env python3
"""
npm_webhook_handler.py -- ZO-SENTINEL npm webhook handler.
FastAPI service on port 8786 that receives npm registry change notifications.
Parses MCP-related packages and registers them in the sentinel system.
"""
import hashlib
import hmac
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Header
import requests

SERVICE_NAME = 'npm_webhook_handler'
PORT = 8786
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
QUERY_URL = 'http://127.0.0.1:8773'
HEARTBEAT_INTERVAL = 60
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')

log = logging.getLogger(__name__)
app = FastAPI(title="npm Webhook Handler", version="1.0.0")

def ws_query(sql: str) -> list:
    """Query via inference router."""
    try:
        response = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result.get('data', [])
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> bool:
    """Write to write_service."""
    try:
        response = requests.post(WRITE_SERVICE_URL, json={
            'table': table,
            'rows': rows,
            'wait': wait
        }, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed: {e}")
        return False

def ws_execute(sql: str) -> bool:
    """Execute SQL via write_service execute endpoint."""
    try:
        response = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {e}")
        return False

def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.warning(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup(signum, frame):
        if os.path.exists(pid_file):
            os.remove(pid_file)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def send_heartbeat():
    """Send service heartbeat."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        }, wait=False)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def compute_server_id(name: str, source: str = 'npm') -> str:
    """Compute unique server ID from package name."""
    raw = f"{source}:{name.lower()}"
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def is_mcp_package(name: str) -> bool:
    """Check if package is MCP-related."""
    name_lower = name.lower()
    mcp_indicators = [
        'modelcontextprotocol',
        'mcp-server',
        'mcp_server',
        'mcp-',
        '-mcp',
        '@modelcontextprotocol',
        'mcp-sdk',
        'mcp-sdk-js',
        'mcp-sdk-python'
    ]
    return any(indicator in name_lower for indicator in mcp_indicators)

def verify_npm_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify npm webhook signature using HMAC-SHA256."""
    if not secret:
        log.warning("WEBHOOK_SECRET not set, skipping signature verification")
        return True
    
    if not signature:
        return False
    
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(f"sha256={expected}", signature)

def register_npm_server(data: Dict[str, Any]) -> Optional[str]:
    """Register npm package as MCP server in registry."""
    name = data.get('name', '')
    version = data.get('version', '')
    description = data.get('description', '') or ''
    tarball = data.get('dist', {}).get('tarball', '')
    
    server_id = compute_server_id(name, 'npm')
    
    existing = ws_query(f"""
        SELECT server_id FROM mcp_server_registry 
        WHERE server_id = '{server_id}'
    """)
    
    now = datetime.now(timezone.utc).isoformat()
    
    if existing:
        log.info(f"Updating existing server: {server_id}")
        sql = f"""
            UPDATE mcp_server_registry SET
                name = '{name.replace("'", "''")}',
                description = '{description.replace("'", "''")}' ,
                url = '{tarball}',
                last_seen = '{now}',
                last_assessed = NULL,
                verdict = NULL,
                trust_score = NULL
            WHERE server_id = '{server_id}'
        """
        ws_execute(sql)
    else:
        log.info(f"Registering new server: {server_id} ({name})")
        
        max_id_result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM mcp_server_registry")
        next_id = max_id_result[0]['next_id'] if max_id_result else 1
        
        sql = f"""
            INSERT INTO mcp_server_registry (id, server_id, name, registry_source, url, description, first_seen, last_seen, scan_count)
            VALUES ({next_id}, '{server_id}', '{name.replace("'", "''")}', 'npm', '{tarball}', '{description.replace("'", "''")}', '{now}', '{now}', 1)
        """
        if not ws_execute(sql):
            log.error(f"Failed to insert server: {server_id}")
            return None
    
    return server_id

def trigger_signal_scoring(server_id: str, source: str = 'npm'):
    """Trigger signal scoring via mesh_memory directive."""
    now = datetime.now(timezone.utc).isoformat()
    
    ws_write('mesh_memory', {
        'event_type': 'scoring_trigger',
        'source': source,
        'server_id': server_id,
        'triggered_at': now,
        'reason': 'npm_webhook_new_package'
    })
    
    log.info(f"Triggered signal scoring for {server_id}")

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": SERVICE_NAME, "port": PORT}

@app.post("/webhook/npm")
async def handle_npm_webhook(request: Request, x_npm_signature: Optional[str] = Header(None)):
    """Handle incoming npm registry webhook notifications."""
    body = await request.body()
    
    if WEBHOOK_SECRET and x_npm_signature:
        if not verify_npm_signature(body, x_npm_signature, WEBHOOK_SECRET):
            log.warning("Invalid npm webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    name = payload.get('name', '')
    
    if not is_mcp_package(name):
        return {
            "accepted": True,
            "server_id": None,
            "skipped": True,
            "reason": "Not an MCP-related package"
        }
    
    version = payload.get('version', 'unknown')
    description = payload.get('description', '')
    tarball = payload.get('dist', {}).get('tarball', '')
    
    log.info(f"Processing MCP package: {name} v{version}")
    
    server_id = register_npm_server(payload)
    
    if server_id:
        trigger_signal_scoring(server_id, 'npm')
        return {
            "accepted": True,
            "server_id": server_id,
            "name": name,
            "version": version,
            "registered": True
        }
    else:
        return {
            "accepted": False,
            "server_id": None,
            "error": "Failed to register server"
        }

def heartbeat_loop():
    """Background heartbeat loop."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

import threading

def run():
    """Run the npm webhook handler daemon."""
    check_single_instance()
    
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

if __name__ == "__main__":
    run()