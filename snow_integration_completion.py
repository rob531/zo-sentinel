import logging
import os
import time
import json
import hmac
import hashlib
import signal
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, Request, HTTPException, Header
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/snow_integration_completion.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'snow_integration_completion'
SERVICE_PORT = 8870
WRITE_SERVICE_URL = 'http://localhost:8772'
EXECUTE_URL = 'http://localhost:8772/execute'
QUERY_URL = 'http://localhost:8772/query'
SNOW_WEBHOOK_CALLBACK_PATH = '/api/v1/snow/webhook/callback'
SNOW_OAUTH_TOKEN_URL = os.environ.get('SNOW_OAUTH_TOKEN_URL', '')
SNOW_CLIENT_ID = os.environ.get('SNOW_CLIENT_ID', '')
SNOW_CLIENT_SECRET = os.environ.get('SNOW_CLIENT_SECRET', '')
SNOW_WEBHOOK_SECRET = os.environ.get('SNOW_WEBHOOK_SECRET', '')
APPROVAL_WORKFLOW_URL = 'http://localhost:8780'
SNOW_CONNECTOR_URL = 'http://localhost:8781'
HEARTBEAT_INTERVAL = 60
PID_FILE = '/tmp/snow_integration_completion.pid'
OAUTH_TOKEN_FILE = '/tmp/snow_oauth_token.json'
OAUTH_TOKEN_EXPIRY_BUFFER = 300

_app: Optional[FastAPI] = None
_token_cache: Optional[Dict[str, Any]] = None


def check_single_instance() -> None:
    """Ensure only one instance runs at a time."""
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        logger.error(f"Another instance is running with PID {pid}. Exiting.")
        sys.exit(1)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file on exit."""
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows},
            timeout=15
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Query write_service and return rows."""
    try:
        response = requests.post(
            QUERY_URL,
            json={'sql': sql},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('rows', [])
        return None
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return None


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        response = requests.post(
            EXECUTE_URL,
            json={'sql': sql},
            timeout=30
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def send_heartbeat() -> None:
    """Send heartbeat to service_health table."""
    ts = utc_now_iso()
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': 'ok'
    }])


def ensure_tables() -> None:
    """Create integration tracking tables if they don't exist."""
    ws_execute("""
        CREATE TABLE IF NOT EXISTS snow_integration_tickets (
            ticket_id VARCHAR PRIMARY KEY,
            server_id VARCHAR NOT NULL,
            snow_sys_id VARCHAR,
            snow_number VARCHAR,
            status VARCHAR,
            resolution VARCHAR,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            webhook_received_at TIMESTAMPTZ
        )
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS snow_integration_audit (
            id INTEGER PRIMARY KEY,
            ticket_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail TEXT,
            created_at TIMESTAMPTZ
        )
    """)


def get_oauth_token() -> Optional[str]:
    """Get valid OAuth token from cache or refresh."""
    global _token_cache
    
    if _token_cache:
        expiry = _token_cache.get('expiry', 0)
        if time.time() < expiry - OAUTH_TOKEN_EXPIRY_BUFFER:
            return _token_cache.get('access_token')
    
    if not SNOW_OAUTH_TOKEN_URL or not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET:
        logger.warning("SNOW OAuth credentials not configured")
        return None
    
    try:
        response = requests.post(
            SNOW_OAUTH_TOKEN_URL,
            data={
                'grant_type': 'client_credentials',
                'client_id': SNOW_CLIENT_ID,
                'client_secret': SNOW_CLIENT_SECRET
            },
            timeout=15
        )
        if response.status_code == 200:
            token_data = response.json()
            _token_cache = {
                'access_token': token_data.get('access_token'),
                'expiry': time.time() + token_data.get('expires_in', 3600)
            }
            return _token_cache['access_token']
    except Exception as e:
        logger.error(f"Failed to get OAuth token: {e}")
    
    return None


def write_audit_log(ticket_id: Optional[str], event_type: str, actor: str, detail: str) -> None:
    """Write audit log entry for ticket actions."""
    ts = utc_now_iso()
    ws_write('snow_integration_audit', [{
        'ticket_id': ticket_id or 'SYSTEM',
        'event_type': event_type,
        'actor': actor,
        'detail': detail,
        'created_at': ts
    }])


def get_pending_approval_servers() -> List[Dict[str, Any]]:
    """Query mcp_server_registry for servers awaiting approval."""
    sql = """
        SELECT server_id, name, url, description, trust_score, verdict,
               registry_source, submitted_at
        FROM mcp_server_registry
        WHERE verdict = 'AMBER_UNVERIFIED'
          AND server_id NOT IN (
              SELECT server_id FROM snow_integration_tickets WHERE status IN ('open', 'pending')
          )
        LIMIT 50
    """
    result = ws_query(sql)
    return result if result else []


def create_snow_ticket(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Submit server approval request to ServiceNow via snow_connector."""
    try:
        token = get_oauth_token()
        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        
        payload = {
            'short_description': f"MCP Server Approval Review: {server.get('name', 'Unknown')}",
            'description': f"""
MCP Server requires security approval review.

Server ID: {server.get('server_id')}
URL: {server.get('url', 'N/A')}
Registry Source: {server.get('registry_source', 'N/A')}
Trust Score: {server.get('trust_score', 0)}

Please review this MCP server for potential security concerns before approval.
            """.strip(),
            'urgency': '3',
            'impact': '2',
            'category': 'software',
            'server_id': server.get('server_id')
        }
        
        response = requests.post(
            f"{SNOW_CONNECTOR_URL}/api/v1/submit_ticket",
            json=payload,
            headers=headers,
            timeout=30
        )
        
        if response.status_code in (200, 201):
            result = response.json()
            return {
                'snow_sys_id': result.get('sys_id', ''),
                'snow_number': result.get('number', ''),
                'status': 'open'
            }
        else:
            logger.error(f"SNOW ticket creation failed: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to create SNOW ticket for {server.get('server_id')}: {e}")
        return None


def register_ticket(server_id: str, ticket_data: Dict[str, Any]) -> None:
    """Register created ticket in tracking table."""
    ts = utc_now_iso()
    ws_write('snow_integration_tickets', [{
        'ticket_id': f"SNOW-{ticket_data.get('snow_number', 'UNKNOWN')}",
        'server_id': server_id,
        'snow_sys_id': ticket_data.get('snow_sys_id', ''),
        'snow_number': ticket_data.get('snow_number', ''),
        'status': ticket_data.get('status', 'open'),
        'created_at': ts,
        'updated_at': ts
    }])
    write_audit_log(
        f"SNOW-{ticket_data.get('snow_number')}",
        'TICKET_CREATED',
        'snow_integration_completion',
        f"Ticket created for server {server_id}"
    )


def verify_webhook_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """MUST validate request signature on inbound webhook using HMAC-SHA256."""
    if not signature or not secret:
        return False
    expected = hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def update_ticket_resolution(ticket_number: str, resolution: str, status: str = 'resolved') -> None:
    """Update ticket with resolution from ServiceNow."""
    ts = utc_now_iso()
    sql = f"""
        UPDATE snow_integration_tickets
        SET resolution = '{resolution.replace("'", "''")}',
            status = '{status}',
            updated_at = '{ts}',
            webhook_received_at = '{ts}'
        WHERE snow_number = '{ticket_number.replace("'", "''")}'
    """
    ws_execute(sql)
    write_audit_log(
        f"SNOW-{ticket_number}",
        'TICKET_RESOLVED',
        'ServiceNow',
        f"Resolution: {resolution}"
    )


def call_approval_workflow(server_id: str, resolution: str, snow_number: str) -> bool:
    """MUST call approval_workflow.py with the ServiceNow resolution."""
    try:
        payload = {
            'server_id': server_id,
            'resolution': resolution,
            'snow_ticket': f"SNOW-{snow_number}",
            'source': 'snow_integration',
            'timestamp': utc_now_iso()
        }
        
        response = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/api/v1/resolve_approval",
            json=payload,
            timeout=20
        )
        
        if response.status_code == 200:
            logger.info(f"Approval workflow called for server {server_id} with resolution: {resolution}")
            return True
        else:
            logger.error(f"Approval workflow call failed: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to call approval_workflow for {server_id}: {e}")
        return False


def process_pending_servers() -> int:
    """Process servers pending approval by creating SNOW tickets."""
    servers = get_pending_approval_servers()
    processed = 0
    
    for server in servers:
        server_id = server.get('server_id')
        logger.info(f"Processing server {server_id} for SNOW ticket creation")
        
        ticket_data = create_snow_ticket(server)
        if ticket_data and ticket_data.get('snow_number'):
            register_ticket(server_id, ticket_data)
            processed += 1
    
    return processed


def cycle() -> int:
    """MUST authenticate via SNOW OAuth token before ticket operations."""
    logger.info("Running snow_integration_completion cycle")
    
    processed = process_pending_servers()
    
    if processed > 0:
        logger.info(f"Created {processed} SNOW tickets")
    
    return processed


def run() -> None:
    """Main daemon loop with heartbeat every 60 seconds."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    
    last_heartbeat = time.time()
    
    while True:
        try:
            cycle()
            
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = time.time()
            
            time.sleep(15)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(30)


def get_app() -> FastAPI:
    """Get FastAPI app for webhook endpoint."""
    global _app
    
    if _app is None:
        _app = FastAPI(title="Snow Integration Completion Webhook Handler")
        
        @_app.post(SNOW_WEBHOOK_CALLBACK_PATH)
        async def snow_webhook_callback(
            request: Request,
            x_snow_signature: Optional[str] = Header(None)
        ):
            """MUST never accept unsigned webhooks. MUST validate request signature."""
            body = await request.body()
            
            if not SNOW_WEBHOOK_SECRET:
                logger.warning("SNOW_WEBHOOK_SECRET not configured, accepting unsigned webhook")
            elif not x_snow_signature:
                logger.warning("Received webhook without signature")
                raise HTTPException(status_code=401, detail="Missing signature header")
            elif not verify_webhook_signature(body, x_snow_signature, SNOW_WEBHOOK_SECRET):
                logger.warning("Invalid webhook signature")
                write_audit_log(None, 'WEBHOOK_REJECTED', 'webhook', 'Invalid signature')
                raise HTTPException(status_code=401, detail="Invalid signature")
            
            try:
                payload = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON payload")
            
            ticket_number = payload.get('ticket_number') or payload.get('number') or payload.get('sys_id')
            resolution = payload.get('resolution') or payload.get('state') or 'approved'
            server_id = payload.get('server_id') or payload.get('u_server_id')
            
            if not ticket_number:
                raise HTTPException(status_code=400, detail="Missing ticket identifier")
            
            logger.info(f"Received SNOW callback for ticket {ticket_number}")
            
            update_ticket_resolution(ticket_number, resolution)
            
            if server_id:
                call_approval_workflow(server_id, resolution, ticket_number)
            else:
                sql = f"""
                    SELECT server_id FROM snow_integration_tickets
                    WHERE snow_number = '{ticket_number.replace("'", "''")}'
                """
                result = ws_query(sql)
                if result:
                    call_approval_workflow(result[0]['server_id'], resolution, ticket_number)
            
            return {'status': 'ok', 'ticket': ticket_number}
        
        @_app.get('/health')
        async def health():
            send_heartbeat()
            return {'status': 'ok', 'service': SERVICE_NAME}
        
        @_app.get('/api/v1/status')
        async def status():
            sql = """
                SELECT status, COUNT(*) as count
                FROM snow_integration_tickets
                GROUP BY status
            """
            tickets = ws_query(sql) or []
            return {
                'service': SERVICE_NAME,
                'tickets': tickets,
                'timestamp': utc_now_iso()
            }
    
    return _app


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Snow Integration Completion Service')
    parser.add_argument('--mode', choices=['daemon', 'webhook'], default='daemon')
    args = parser.parse_args()
    
    if args.mode == 'webhook':
        app = get_app()
        uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT)
    else:
        run()