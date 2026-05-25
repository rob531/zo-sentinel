#!/usr/bin/env python3
"""
exemption_manager.py -- ZO-SENTINEL Exemption Manager.
Handles cases where an MCP is approved despite negative signals.
Exemptions override policy_engine ESCALATE decisions to CONDITIONAL_ALLOW.
"""
import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8773/query"

EXEMPTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS mcp_exemptions (
    id               BIGINT PRIMARY KEY,
    server_id        VARCHAR NOT NULL,
    reason           TEXT,
    granted_by       VARCHAR,
    expires_at       TIMESTAMPTZ,
    conditions_json  TEXT,
    active           BOOLEAN DEFAULT TRUE,
    revoked_by       VARCHAR,
    revoked_at       TIMESTAMPTZ,
    granted_at       TIMESTAMPTZ DEFAULT now()
)
"""


def ws_execute(sql: str) -> bool:
    """Execute SQL via write_service."""
    try:
        response = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        log.error(f"Execute failed: {e}")
        return False


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service using 'rows' field."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        log.error(f"Write failed: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query data via inference_router."""
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30
        )
        if response.status_code == 200:
            return response.json().get("rows", [])
        return []
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ensure_exemptions_table() -> bool:
    """Create mcp_exemptions table if it doesn't exist."""
    return ws_execute(EXEMPTIONS_TABLE)


def grant_exemption(
    server_id: str,
    reason: str,
    granted_by: str,
    expires_days: int = 30,
    conditions: Optional[List[str]] = None
) -> str:
    """
    Grant an exemption for a server.
    
    Args:
        server_id: The server's unique identifier
        reason: Justification for the exemption
        granted_by: Who granted this exemption
        expires_days: Number of days until expiration (default 30)
        conditions: List of conditions that must be met
        
    Returns:
        submission_id of the exemption record
    """
    if not ensure_exemptions_table():
        raise RuntimeError("Failed to ensure exemptions table exists")
    
    expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    conditions_json = json.dumps(conditions or [])
    
    # Generate a unique submission_id
    submission_id = f"EXEMPT-{server_id[:8]}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # First, get the next id
    id_result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM mcp_exemptions")
    next_id = id_result[0]["next_id"] if id_result else 1
    
    rows = [{
        "id": next_id,
        "server_id": server_id,
        "reason": reason,
        "granted_by": granted_by,
        "expires_at": expires_at,
        "conditions_json": conditions_json,
        "active": True
    }]
    
    if not ws_write("mcp_exemptions", rows):
        raise RuntimeError("Failed to write exemption record")
    
    log.info(f"Granted exemption {submission_id} for server {server_id}, expires at {expires_at}")
    return submission_id


def check_exemption(server_id: str) -> Optional[Dict[str, Any]]:
    """
    Check if a server has an active, non-expired exemption.
    
    Args:
        server_id: The server's unique identifier
        
    Returns:
        Exemption record dict if active and not expired, None otherwise
    """
    sql = f"""
    SELECT * FROM mcp_exemptions 
    WHERE server_id = '{server_id}' 
    AND active = TRUE 
    AND expires_at > now()
    ORDER BY granted_at DESC
    LIMIT 1
    """
    
    results = ws_query(sql)
    if results:
        exemption = results[0]
        # Parse conditions_json if present
        if exemption.get("conditions_json"):
            try:
                exemption["conditions"] = json.loads(exemption["conditions_json"])
            except (json.JSONDecodeError, TypeError):
                exemption["conditions"] = []
        return exemption
    
    return None


def revoke_exemption(server_id: str, revoked_by: str) -> bool:
    """
    Revoke an active exemption for a server.
    
    Args:
        server_id: The server's unique identifier
        revoked_by: Who is revoking the exemption
        
    Returns:
        True if revocation was successful, False otherwise
    """
    sql = f"""
    UPDATE mcp_exemptions 
    SET active = FALSE, 
        revoked_by = '{revoked_by}', 
        revoked_at = now() 
    WHERE server_id = '{server_id}' 
    AND active = TRUE
    """
    
    if ws_execute(sql):
        log.info(f"Revoked exemption for server {server_id} by {revoked_by}")
        return True
    
    log.warning(f"Failed to revoke exemption for server {server_id}")
    return False


def list_expiring_exemptions(days: int = 7) -> List[Dict[str, Any]]:
    """
    List exemptions expiring within the specified number of days.
    
    Args:
        days: Number of days to look ahead (default 7)
        
    Returns:
        List of exemption records expiring soon
    """
    sql = f"""
    SELECT * FROM mcp_exemptions 
    WHERE active = TRUE 
    AND expires_at > now() 
    AND expires_at <= now() + INTERVAL '{days} days'
    ORDER BY expires_at ASC
    """
    
    return ws_query(sql)


def check_and_warn_expiring_exemptions(days: int = 7) -> List[Dict[str, Any]]:
    """
    Check for expiring exemptions and emit warnings.
    Called by daily digest or scheduler.
    
    Args:
        days: Number of days to look ahead (default 7)
        
    Returns:
        List of expiring exemptions found
    """
    expiring = list_expiring_exemptions(days)
    
    for exemption in expiring:
        log.warning(
            f"Exemption expiring soon for server {exemption['server_id']}: "
            f"expires at {exemption['expires_at']}, "
            f"reason: {exemption.get('reason', 'N/A')}"
        )
    
    return expiring


def get_override_verdict(exemption: Dict[str, Any]) -> str:
    """
    Get the override verdict for an exempt server.
    Exemptions override ESCALATE to CONDITIONAL_ALLOW.
    
    Args:
        exemption: The exemption record
        
    Returns:
        'CONDITIONAL_ALLOW' if conditions exist, otherwise 'CONDITIONAL_ALLOW' 
        with conditions attached
    """
    return "CONDITIONAL_ALLOW"


def get_exemption_with_conditions(server_id: str) -> Optional[Dict[str, Any]]:
    """
    Get exemption details including parsed conditions.
    
    Args:
        server_id: The server's unique identifier
        
    Returns:
        Exemption record with parsed conditions, or None
    """
    exemption = check_exemption(server_id)
    if exemption and exemption.get("conditions"):
        return exemption
    return None


def run() -> None:
    """Initialize and validate exemption manager."""
    log.info("Initializing exemption manager...")
    
    if ensure_exemptions_table():
        log.info("Exemption manager initialized successfully")
    else:
        log.error("Failed to initialize exemption manager")
        raise RuntimeError("Exemption table initialization failed")


if __name__ == "__main__":
    run()