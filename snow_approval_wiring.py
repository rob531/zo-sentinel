import os
import sys
import logging
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests

# Service endpoints
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://localhost:8772')
QUERY_SERVICE_URL = os.environ.get('QUERY_SERVICE_URL', 'http://localhost:8772')

# ServiceNow configuration
SNOW_INSTANCE = os.environ.get('SNOW_INSTANCE', '')
SNOW_CLIENT_ID = os.environ.get('SNOW_CLIENT_ID', '')
SNOW_CLIENT_SECRET = os.environ.get('SNOW_CLIENT_SECRET', '')
SNOW_OAUTH_TOKEN = os.environ.get('SNOW_OAUTH_TOKEN', '')
SNOW_OAUTH_TOKEN_FILE = os.environ.get('snow_oauth_token_file', '/tmp/snow_oauth_token.json')
SNOW_WEBHOOK_SECRET = os.environ.get('SNOW_WEBHOOK_SECRET', '')

# HTTP timeouts
REQUEST_TIMEOUT = 30
OAUTH_TIMEOUT = 15

# Logger setup - only basicConfig in entrypoint, use getLogger in library modules
log = logging.getLogger('snow_approval_wiring')

# Token cache
_token_cache: Optional[Dict[str, Any]] = None


def get_token_path() -> Path:
    """Get the path to the Snow OAuth token file."""
    return Path(SNOW_OAUTH_TOKEN_FILE)


def load_token_from_file() -> Optional[str]:
    """Load OAuth token from file if it exists and is valid."""
    token_path = get_token_path()
    if not token_path.exists():
        return None
    try:
        import json
        with open(token_path, 'r') as f:
            data = json.load(f)
        access_token = data.get('access_token', '')
        expires_at = data.get('expires_at', 0)
        # Check if token is still valid (with 60s buffer)
        if access_token and expires_at > (time.time() + 60):
            return access_token
    except Exception:
        pass
    return None


def save_token_to_file(token_data: Dict[str, Any]) -> bool:
    """Save OAuth token to file."""
    try:
        token_path = get_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(token_path, 'w') as f:
            json.dump(token_data, f)
        return True
    except Exception:
        return False


def get_snow_oauth_token() -> Optional[str]:
    """
    Get Snow OAuth token from env or file with caching.
    Returns None if no valid token available.
    """
    global _token_cache
    
    # Check direct environment variable first (highest priority)
    if SNOW_OAUTH_TOKEN:
        log.debug("Using OAuth token from environment variable")
        return SNOW_OAUTH_TOKEN
    
    # Check cache first
    if _token_cache:
        expires_at = _token_cache.get('expires_at', 0)
        if time.time() < (expires_at - 60):
            return _token_cache.get('access_token')
    
    # Check file cache
    file_token = load_token_from_file()
    if file_token:
        log.debug("Using OAuth token from file cache")
        return file_token
    
    # If we have credentials, acquire a new token
    if SNOW_CLIENT_ID and SNOW_CLIENT_SECRET and SNOW_INSTANCE:
        return _acquire_oauth_token()
    
    log.warning("No Snow OAuth token available")
    return None


def _acquire_oauth_token() -> Optional[str]:
    """
    Acquire new OAuth token from ServiceNow using client credentials flow.
    Returns access_token string or None on failure.
    """
    global _token_cache
    
    if not SNOW_INSTANCE or not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET:
        log.warning("Missing ServiceNow OAuth credentials")
        return None
    
    try:
        token_url = f"https://{SNOW_INSTANCE}.service-now.com/oauth_token.do"
        data = {
            'grant_type': 'client_credentials',
            'client_id': SNOW_CLIENT_ID,
            'client_secret': SNOW_CLIENT_SECRET
        }
        
        response = requests.post(
            token_url,
            data=data,
            timeout=OAUTH_TIMEOUT
        )
        
        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get('access_token')
            expires_in = token_data.get('expires_in', 3600)
            
            # Cache token with expiration
            _token_cache = {
                'access_token': access_token,
                'expires_at': time.time() + expires_in
            }
            
            # Save to file for persistence
            save_token_to_file(_token_cache)
            
            log.info(f"Acquired new OAuth token, expires in {expires_in}s")
            return access_token
        else:
            log.error(f"Failed to acquire OAuth token: {response.status_code}")
            return None
            
    except requests.RequestException as e:
        log.error(f"Request exception acquiring OAuth token: {e}")
        return None


def is_token_fresh() -> bool:
    """Check if current OAuth token is fresh."""
    global _token_cache
    
    if SNOW_OAUTH_TOKEN:
        return True
    
    if _token_cache:
        expires_at = _token_cache.get('expires_at', 0)
        return time.time() < (expires_at - 60)
    
    return False


def validate_snow_webhook_signature(
    signature_header: Optional[str],
    payload_body: bytes,
    secret: Optional[str] = None
) -> bool:
    """
    Validate ServiceNow webhook signature from X-SNOW-Signature header.
    Uses HMAC-SHA256 with the configured webhook secret.
    
    Args:
        signature_header: Value of X-SNOW-Signature header
        payload_body: Raw request body as bytes
        secret: Webhook secret (uses SNOW_WEBHOOK_SECRET env if not provided)
    
    Returns:
        True if signature is valid, False otherwise
    """
    if not signature_header:
        log.warning("No X-SNOW-Signature header present")
        return False
    
    webhook_secret = secret or SNOW_WEBHOOK_SECRET
    if not webhook_secret:
        log.error("No SNOW_WEBHOOK_SECRET configured for signature validation")
        return False
    
    try:
        # Expected signature format: "sha256=<hex_digest>"
        if signature_header.startswith('sha256='):
            received_sig = signature_header[7:]
        else:
            received_sig = signature_header
        
        # Compute expected signature
        expected_sig = hmac.new(
            webhook_secret.encode('utf-8'),
            payload_body,
            hashlib.sha256
        ).hexdigest()
        
        # Use constant-time comparison to prevent timing attacks
        is_valid = hmac.compare_digest(received_sig.lower(), expected_sig.lower())
        
        if is_valid:
            log.debug("Webhook signature validation successful")
        else:
            log.warning(f"Webhook signature mismatch: received={received_sig[:16]}..., expected={expected_sig[:16]}...")
        
        return is_valid
        
    except Exception as e:
        log.error(f"Error validating webhook signature: {e}")
        return False


def ws_query(sql: str, params: Optional[tuple] = None) -> Optional[List[Dict[str, Any]]]:
    """
    Query WriteService for data.
    
    Args:
        sql: SQL query string (parameterized)
        params: Optional tuple of parameters
    
    Returns:
        List of rows as dicts, or None on error
    """
    try:
        payload = {'sql': sql}
        if params:
            payload['params'] = params
        
        response = requests.post(
            QUERY_SERVICE_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get('rows', [])
        else:
            log.error(f"Query failed: {response.status_code} - {response.text[:200]}")
            return None
            
    except requests.RequestException as e:
        log.error(f"Query request exception: {e}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """
    Write to WriteService (DuckDB gateway).
    
    Args:
        table: Table name
        rows: List of row dicts to insert
    
    Returns:
        True on success, False on failure
    """
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows},
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 200:
            log.debug(f"Write successful to {table}: {len(rows)} rows")
            return True
        else:
            log.error(f"Write failed: {response.status_code} - {response.text[:200]}")
            return False
            
    except requests.RequestException as e:
        log.error(f"Write request exception: {e}")
        return False


def get_analyst_approval(server_id: str) -> Optional[str]:
    """
    Query mcp_decisions table for analyst's approval status.
    Returns verdict string (APPROVED, CONDITIONAL, REJECTED, PENDING) or None.
    
    Args:
        server_id: MCP server ID to check
    
    Returns:
        Verdict string or None
    """
    sql = """
    SELECT verdict, created_at
    FROM mcp_decisions
    WHERE server_id = ?
    ORDER BY created_at DESC
    LIMIT 1
    """
    
    rows = ws_query(sql, (server_id,))
    
    if rows and len(rows) > 0:
        return rows[0].get('verdict')
    
    return None


def get_pending_snow_approvals() -> List[Dict[str, Any]]:
    """
    Query for MCP decisions that need to be forwarded to ServiceNow.
    Looks for APPROVED or CONDITIONAL verdicts that haven't been sent to SNOW.
    
    Returns:
        List of decision records to forward
    """
    sql = """
    SELECT 
        d.server_id,
        d.verdict,
        d.created_at,
        r.name as server_name,
        r.url as server_url,
        r.trust_score
    FROM mcp_decisions d
    JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.verdict IN ('APPROVED', 'CONDITIONAL')
      AND d.snow_sync_status IS NULL
    ORDER BY d.created_at DESC
    LIMIT 100
    """
    
    rows = ws_query(sql)
    return rows if rows else []


def create_snow_incident(
    server_id: str,
    server_name: str,
    verdict: str,
    trust_score: Optional[float] = None
) -> Optional[str]:
    """
    Create a ServiceNow incident for MCP approval.
    Uses OAuth token for authentication.
    
    Args:
        server_id: MCP server ID
        server_name: Server name
        verdict: Approval verdict
        trust_score: Optional trust score
    
    Returns:
        Incident number (sys_id) or None on failure
    """
    token = get_snow_oauth_token()
    if not token:
        log.error("No valid Snow OAuth token available")
        return None
    
    if not SNOW_INSTANCE:
        log.error("SNOW_INSTANCE not configured")
        return None
    
    try:
        incident_url = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/incident"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        description = (
            f"MCP Server Approval Request\n"
            f"Server: {server_name}\n"
            f"Server ID: {server_id}\n"
            f"Verdict: {verdict}\n"
        )
        if trust_score is not None:
            description += f"Trust Score: {trust_score}\n"
        
        incident_data = {
            'short_description': f"MCP Approval: {server_name}",
            'description': description,
            'category': 'Security',
            'subcategory': 'MCP Server Approval',
            'impact': '2',
            'urgency': '3',
            'priority': '3'
        }
        
        response = requests.post(
            incident_url,
            headers=headers,
            json=incident_data,
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code in (200, 201):
            result = response.json()
            sys_id = result.get('result', {}).get('sys_id')
            log.info(f"Created Snow incident {sys_id} for server {server_id}")
            return sys_id
        else:
            log.error(f"Failed to create incident: {response.status_code} - {response.text[:200]}")
            return None
            
    except requests.RequestException as e:
        log.error(f"Request exception creating Snow incident: {e}")
        return None


def update_decision_snow_status(
    server_id: str,
    snow_sys_id: str,
    status: str = 'SENT'
) -> bool:
    """
    Update mcp_decisions table with Snow sync status.
    
    Args:
        server_id: MCP server ID
        snow_sys_id: ServiceNow incident sys_id
        status: Sync status (SENT, FAILED)
    
    Returns:
        True on success, False on failure
    """
    now = datetime.now(timezone.utc).isoformat()
    
    sql = """
    UPDATE mcp_decisions
    SET snow_sync_status = ?,
        snow_incident_id = ?,
        snow_synced_at = ?
    WHERE server_id = ?
    """
    
    # Use ws_execute equivalent for UPDATE
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={
                'sql': sql,
                'params': (status, snow_sys_id, now, server_id)
            },
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 200:
            log.debug(f"Updated snow_sync_status for {server_id}")
            return True
        else:
            log.error(f"Failed to update decision status: {response.text[:200]}")
            return False
            
    except requests.RequestException as e:
        log.error(f"Request exception updating decision: {e}")
        return False


def forward_approval_to_snow(server_id: str) -> bool:
    """
    Forward a single approval decision to ServiceNow.
    
    Args:
        server_id: MCP server ID to forward
    
    Returns:
        True on success, False on failure
    """
    # Get the approval decision
    verdict = get_analyst_approval(server_id)
    
    if not verdict:
        log.warning(f"No approval decision found for {server_id}")
        return False
    
    if verdict not in ('APPROVED', 'CONDITIONAL'):
        log.info(f"Verdict {verdict} for {server_id} does not require Snow forwarding")
        return True
    
    # Get server details
    sql = """
    SELECT name, url, trust_score
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    
    rows = ws_query(sql, (server_id,))
    if not rows:
        log.error(f"Server {server_id} not found in registry")
        return False
    
    server_data = rows[0]
    server_name = server_data.get('name', 'Unknown')
    trust_score = server_data.get('trust_score')
    
    # Create Snow incident
    snow_sys_id = create_snow_incident(
        server_id=server_id,
        server_name=server_name,
        verdict=verdict,
        trust_score=trust_score
    )
    
    if snow_sys_id:
        # Update local record
        update_decision_snow_status(server_id, snow_sys_id, 'SENT')
        return True
    else:
        update_decision_snow_status(server_id, '', 'FAILED')
        return False


def sync_pending_approvals() -> Dict[str, int]:
    """
    Sync all pending approvals to ServiceNow.
    
    Returns:
        Dict with 'success' and 'failed' counts
    """
    pending = get_pending_snow_approvals()
    
    results = {'success': 0, 'failed': 0}
    
    for decision in pending:
        server_id = decision.get('server_id')
        try:
            if forward_approval_to_snow(server_id):
                results['success'] += 1
            else:
                results['failed'] += 1
        except Exception as e:
            log.error(f"Error syncing {server_id}: {e}")
            results['failed'] += 1
    
    log.info(f"Sync complete: {results['success']} success, {results['failed']} failed")
    return results


def check_write_service_connectivity() -> bool:
    """
    Check if write service is reachable and responsive.
    
    Returns:
        True if healthy, False otherwise
    """
    try:
        response = requests.get(
            f"{WRITE_SERVICE_URL}/health",
            timeout=5
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def get_snow_oauth_status() -> Dict[str, Any]:
    """
    Get current Snow OAuth token status.
    
    Returns:
        Dict with token status information
    """
    global _token_cache
    
    status = {
        'has_direct_token': bool(SNOW_OAUTH_TOKEN),
        'has_file_token': get_token_path().exists(),
        'has_cached_token': _token_cache is not None,
        'is_token_fresh': is_token_fresh(),
        'configured': bool(SNOW_INSTANCE and SNOW_CLIENT_ID and SNOW_CLIENT_SECRET)
    }
    
    if _token_cache:
        status['expires_at'] = _token_cache.get('expires_at')
        status['expires_in_seconds'] = max(0, _token_cache.get('expires_at', 0) - time.time())
    
    return status


def handle_inbound_webhook(
    signature: Optional[str],
    payload: bytes,
    content_type: str
) -> Dict[str, Any]:
    """
    Handle incoming ServiceNow webhook.
    
    Args:
        signature: X-SNOW-Signature header value
        payload: Raw request body
        content_type: Content-Type header
    
    Returns:
        Response dict with status and message
    """
    # Validate signature first - reject unsigned webhooks
    if not validate_snow_webhook_signature(signature, payload):
        log.warning("Rejected unsigned or invalid webhook")
        return {
            'status': 401,
            'body': {'error': 'Unauthorized', 'message': 'Invalid or missing webhook signature'}
        }
    
    # Parse webhook payload
    try:
        import json
        if 'json' in content_type.lower():
            data = json.loads(payload.decode('utf-8'))
        else:
            data = json.loads(payload.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.error(f"Failed to parse webhook payload: {e}")
        return {
            'status': 400,
            'body': {'error': 'Bad Request', 'message': 'Invalid JSON payload'}
        }
    
    # Process based on webhook type
    webhook_type = data.get('webhook_type', data.get('event_type', ''))
    
    if webhook_type == 'approval_response':
        return handle_approval_response(data)
    elif webhook_type == 'ticket_update':
        return handle_ticket_update(data)
    elif webhook_type == 'incident_created':
        return handle_incident_created(data)
    else:
        log.info(f"Received unhandled webhook type: {webhook_type}")
        return {
            'status': 200,
            'body': {'status': 'acknowledged', 'type': webhook_type}
        }


def handle_approval_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle approval response webhook from ServiceNow.
    
    Args:
        data: Parsed webhook payload
    
    Returns:
        Response dict
    """
    server_id = data.get('server_id')
    snow_ticket = data.get('snow_ticket_id')
    response_verdict = data.get('verdict')
    
    if not server_id:
        return {
            'status': 400,
            'body': {'error': 'Bad Request', 'message': 'Missing server_id'}
        }
    
    # Record the Snow approval response
    now = datetime.now(timezone.utc).isoformat()
    
    approval_record = {
        'server_id': server_id,
        'verdict': response_verdict,
        'snow_ticket_id': snow_ticket,
        'source': 'snow_webhook',
        'received_at': now
    }
    
    # Write to mcp_decisions via write_service
    success = ws_write('mcp_decisions', [approval_record])
    
    if success:
        return {
            'status': 200,
            'body': {
                'status': 'recorded',
                'server_id': server_id,
                'verdict': response_verdict
            }
        }
    else:
        return {
            'status': 500,
            'body': {'error': 'Internal Error', 'message': 'Failed to record approval'}
        }


def handle_ticket_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle ticket update webhook from ServiceNow.
    
    Args:
        data: Parsed webhook payload
    
    Returns:
        Response dict
    """
    snow_ticket = data.get('ticket_id')
    status = data.get('status')
    notes = data.get('notes', '')
    
    # Update local tracking
    log.info(f"Snow ticket {snow_ticket} updated: status={status}")
    
    return {
        'status': 200,
        'body': {
            'status': 'acknowledged',
            'ticket_id': snow_ticket
        }
    }


def handle_incident_created(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle incident created webhook from ServiceNow.
    
    Args:
        data: Parsed webhook payload
    
    Returns:
        Response dict
    """
    snow_sys_id = data.get('sys_id')
    short_description = data.get('short_description', '')
    
    log.info(f"New Snow incident created: {snow_sys_id} - {short_description}")
    
    return {
        'status': 200,
        'body': {
            'status': 'acknowledged',
            'sys_id': snow_sys_id
        }
    }


# === Exported Functions for approval_workflow.py integration ===

def check_service_health() -> Dict[str, Any]:
    """
    Check health of all Snow-related services.
    
    Returns:
        Dict with health status of each component
    """
    snow_status = get_snow_oauth_status()
    ws_healthy = check_write_service_connectivity()
    
    return {
        'oauth': snow_status,
        'write_service': ws_healthy,
        'overall': snow_status['configured'] and ws_healthy
    }


def get_registry_verdict(server_id: str) -> Optional[str]:
    """
    Get current verdict for a server from mcp_decisions.
    Convenience function for approval_workflow integration.
    
    Args:
        server_id: MCP server ID
    
    Returns:
        Verdict string or None
    """
    return get_analyst_approval(server_id)


def record_approval_decision(
    server_id: str,
    verdict: str,
    analyst_email: str,
    notes: Optional[str] = None
) -> bool:
    """
    Record an approval decision in mcp_decisions table.
    
    Args:
        server_id: MCP server ID
        verdict: APPROVED, CONDITIONAL, or REJECTED
        analyst_email: Analyst who made the decision
        notes: Optional notes
    
    Returns:
        True on success, False on failure
    """
    now = datetime.now(timezone.utc).isoformat()
    
    record = {
        'server_id': server_id,
        'verdict': verdict,
        'analyst_email': analyst_email,
        'notes': notes,
        'created_at': now,
        'source': 'approval_workflow'
    }
    
    return ws_write('mcp_decisions', [record])


def get_pending_snow_tickets() -> List[Dict[str, Any]]:
    """
    Get approvals pending Snow sync.
    Convenience wrapper for external callers.
    
    Returns:
        List of pending decisions
    """
    return get_pending_snow_approvals()


if __name__ == '__main__':
    # Run diagnostics when executed directly
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
    )
    
    print("=== Snow Approval Wiring Diagnostics ===\n")
    
    # Check OAuth status
    oauth_status = get_snow_oauth_status()
    print("OAuth Status:")
    for k, v in oauth_status.items():
        print(f"  {k}: {v}")
    print()
    
    # Check write service connectivity
    ws_healthy = check_write_service_connectivity()
    print(f"Write Service: {'HEALTHY' if ws_healthy else 'UNREACHABLE'}\n")
    
    # Check pending approvals
    pending = get_pending_snow_approvals()
    print(f"Pending Snow approvals: {len(pending)}")
    for p in pending[:5]:
        print(f"  - {p.get('server_id')}: {p.get('verdict')}")
    print()
    
    # Overall health
    health = check_service_health()
    print(f"Overall Status: {'HEALTHY' if health['overall'] else 'DEGRADED'}")