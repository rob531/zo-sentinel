import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/admin_exemptions_api.log')]
)
log = logging.getLogger('admin_exemptions_api')

SERVICE_NAME = 'admin_exemptions_api'
SERVICE_PORT = 8791
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
HTTP_TIMEOUT = 30

app = FastAPI(title='Admin Exemptions API', version='1.0.0')

class ExemptionCreate(BaseModel):
    server_id: str = Field(..., description="MCP server ID to exempt")
    reason: str = Field(..., description="Reason for exemption")
    duration_days: int = Field(default=30, ge=1, le=365, description="Duration in days")
    granted_by: str = Field(..., description="Admin granting the exemption")
    risk_tier_override: Optional[str] = Field(None, description="Override risk tier if needed")


class ExemptionUpdate(BaseModel):
    reason: Optional[str] = None
    duration_days: Optional[int] = Field(None, ge=1, le=365)
    risk_tier_override: Optional[str] = None


class ExemptionResponse(BaseModel):
    exemption_id: str
    server_id: str
    reason: str
    granted_by: str
    expires_at: str
    created_at: str
    risk_tier_override: Optional[str] = None
    status: str = 'active'


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
        result = response.json()
        return result.get('rows', [])
    except requests.exceptions.RequestException as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=HTTP_TIMEOUT
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Execute failed: {e}")
        return False


def compute_exemption_id(server_id: str, granted_by: str) -> str:
    """Generate deterministic exemption ID."""
    import hashlib
    content = f"{server_id}:{granted_by}:{datetime.now(timezone.utc).isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def compute_expiry_timestamp(days: int) -> str:
    """Compute expiry timestamp from now + days."""
    from datetime import timedelta
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    return expiry.isoformat() + 'Z'


def ensure_exemptions_table() -> None:
    """Ensure the exemption_exemptions table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS exemption_exemptions (
        exemption_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        reason VARCHAR NOT NULL,
        granted_by VARCHAR NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        risk_tier_override VARCHAR,
        status VARCHAR DEFAULT 'active'
    )
    """
    ws_execute(sql)


def get_exemption_by_id(exemption_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single exemption by ID."""
    sql = f"SELECT * FROM exemption_exemptions WHERE exemption_id = '{exemption_id}'"
    rows = ws_query(sql)
    return rows[0] if rows else None


def get_exemption_by_server(server_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve active exemption for a server."""
    sql = f"SELECT * FROM exemption_exemptions WHERE server_id = '{server_id}' AND status = 'active'"
    rows = ws_query(sql)
    return rows[0] if rows else None


def list_exemptions(limit: int = 100, offset: int = 0, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List exemptions with optional status filter."""
    if status_filter:
        sql = f"SELECT * FROM exemption_exemptions WHERE status = '{status_filter}' ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    else:
        sql = f"SELECT * FROM exemption_exemptions ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    return ws_query(sql)


def create_exemption(data: ExemptionCreate) -> Dict[str, Any]:
    """Create a new exemption."""
    existing = get_exemption_by_server(data.server_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Active exemption already exists for server {data.server_id}"
        )
    
    exemption_id = compute_exemption_id(data.server_id, data.granted_by)
    created_at = datetime.now(timezone.utc).isoformat() + 'Z'
    expires_at = compute_expiry_timestamp(data.duration_days)
    
    row = {
        'exemption_id': exemption_id,
        'server_id': data.server_id,
        'reason': data.reason,
        'granted_by': data.granted_by,
        'expires_at': expires_at,
        'created_at': created_at,
        'risk_tier_override': data.risk_tier_override,
        'status': 'active'
    }
    
    if ws_write('exemption_exemptions', [row]):
        return row
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create exemption"
        )


def update_exemption(exemption_id: str, data: ExemptionUpdate) -> Dict[str, Any]:
    """Update an existing exemption."""
    existing = get_exemption_by_id(exemption_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exemption {exemption_id} not found"
        )
    
    updates = []
    if data.reason is not None:
        updates.append(f"reason = '{data.reason}'")
    if data.duration_days is not None:
        new_expires = compute_expiry_timestamp(data.duration_days)
        updates.append(f"expires_at = '{new_expires}'")
    if data.risk_tier_override is not None:
        updates.append(f"risk_tier_override = '{data.risk_tier_override}'")
    
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )
    
    sql = f"UPDATE exemption_exemptions SET {', '.join(updates)} WHERE exemption_id = '{exemption_id}'"
    
    if ws_execute(sql):
        return get_exemption_by_id(exemption_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update exemption"
        )


def revoke_exemption(exemption_id: str, revoked_by: str) -> Dict[str, Any]:
    """Revoke an exemption (soft delete)."""
    existing = get_exemption_by_id(exemption_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exemption {exemption_id} not found"
        )
    
    revoked_at = datetime.now(timezone.utc).isoformat() + 'Z'
    sql = f"UPDATE exemption_exemptions SET status = 'revoked' WHERE exemption_id = '{exemption_id}'"
    
    if ws_execute(sql):
        return {**existing, 'status': 'revoked', 'revoked_at': revoked_at, 'revoked_by': revoked_by}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke exemption"
        )


def check_exemption_active(server_id: str) -> bool:
    """Check if a server has an active exemption."""
    sql = f"SELECT COUNT(*) as cnt FROM exemption_exemptions WHERE server_id = '{server_id}' AND status = 'active' AND expires_at > NOW()"
    rows = ws_query(sql)
    return rows[0]['cnt'] > 0 if rows else False


def cleanup_expired_exemptions() -> int:
    """Mark expired exemptions as expired. Returns count of updated rows."""
    sql = "UPDATE exemption_exemptions SET status = 'expired' WHERE status = 'active' AND expires_at < NOW()"
    try:
        response = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        return result.get('affected_rows', 0)
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to cleanup expired exemptions: {e}")
        return 0


@app.on_event("startup")
async def startup_event():
    """Initialize on startup."""
    log.info(f"Starting {SERVICE_NAME}")
    ensure_exemptions_table()
    cleanup_expired_exemptions()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/exemptions")
async def list_exemptions_endpoint(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None
):
    """List all exemptions with pagination."""
    return {"exemptions": list_exemptions(limit, offset, status), "limit": limit, "offset": offset}


@app.get("/exemptions/{exemption_id}")
async def get_exemption(exemption_id: str):
    """Get a single exemption by ID."""
    exemption = get_exemption_by_id(exemption_id)
    if not exemption:
        raise HTTPException(status_code=404, detail="Exemption not found")
    return exemption


@app.get("/exemptions/server/{server_id}")
async def get_server_exemption(server_id: str):
    """Get active exemption for a specific server."""
    exemption = get_exemption_by_server(server_id)
    if not exemption:
        raise HTTPException(status_code=404, detail="No active exemption found for server")
    return exemption


@app.get("/exemptions/check/{server_id}")
async def check_server_exemption(server_id: str):
    """Check if a server has an active exemption."""
    is_exempt = check_exemption_active(server_id)
    return {"server_id": server_id, "exempt": is_exempt}


@app.post("/exemptions", status_code=status.HTTP_201_CREATED)
async def create_exemption_endpoint(data: ExemptionCreate):
    """Create a new exemption."""
    return create_exemption(data)


@app.patch("/exemptions/{exemption_id}")
async def update_exemption_endpoint(exemption_id: str, data: ExemptionUpdate):
    """Update an existing exemption."""
    return update_exemption(exemption_id, data)


@app.delete("/exemptions/{exemption_id}")
async def revoke_exemption_endpoint(exemption_id: str, revoked_by: str):
    """Revoke an exemption."""
    return revoke_exemption(exemption_id, revoked_by)


@app.post("/exemptions/cleanup")
async def cleanup_expired():
    """Manually trigger cleanup of expired exemptions."""
    count = cleanup_expired_exemptions()
    return {"cleaned": count}


def run():
    """Entry point for running the service."""
    import uvicorn
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT)


if __name__ == '__main__':
    run()