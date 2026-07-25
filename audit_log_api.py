"""
Sentinel Product Audit Log API

Provides structured audit trail for all Sentinel product actions.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
import requests

# Required for data layer - audit_log accessed via write_service, not app tables
from app.db import get_session
from app.models import AuditLog

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"


class AuditEventCreate(BaseModel):
    """Input schema for creating an audit event."""
    action: str = Field(..., description="Action type (e.g. verdict_override, score_dispute_resolved)")
    actor: str = Field(..., description="User email or service name that initiated the action")
    org_id: str = Field(..., description="Organization ID")
    target_server_id: Optional[str] = Field(None, description="Server being acted upon, if applicable")
    outcome: str = Field(..., description="Outcome: success, failure, or partial")
    metadata: dict = Field(default_factory=dict, description="Arbitrary structured detail")


class AuditEvent(BaseModel):
    """Output schema for an audit event."""
    id: int
    timestamp: str
    action: str
    actor: str
    org_id: str
    target_server_id: Optional[str] = None
    outcome: str
    metadata: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


@router.post("", response_model=AuditEvent)
def create_audit_event(
    event: AuditEventCreate,
    session=Depends(get_session)
) -> AuditEvent:
    """Write an audit event to the audit_log table."""
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    row = {
        "timestamp": timestamp,
        "action": event.action,
        "actor": event.actor,
        "org_id": event.org_id,
        "target_server_id": event.target_server_id,
        "outcome": event.outcome,
        "metadata": event.metadata,
    }
    
    payload = {
        "table": "audit_log",
        "rows": [row],
        "wait": True,
    }
    
    response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Write service error: {response.text}")
    
    result = response.json()
    
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=f"Write service error: {result.get('message')}")
    
    rows_written = result.get("rows_written", 0)
    if rows_written == 0:
        raise HTTPException(status_code=500, detail="No rows written to audit_log")
    
    return AuditEvent(
        id=result.get("last_insert_id", 0),
        timestamp=timestamp,
        action=event.action,
        actor=event.actor,
        org_id=event.org_id,
        target_server_id=event.target_server_id,
        outcome=event.outcome,
        metadata=event.metadata,
    )


@router.get("", response_model=list[AuditEvent])
def get_audit_log(
    org_id: Optional[str] = Query(None, description="Filter by organization ID"),
    actor: Optional[str] = Query(None, description="Filter by actor"),
    action: Optional[str] = Query(None, description="Filter by action type"),
    start_date: Optional[str] = Query(None, description="Filter by start date (ISO 8601)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
) -> list[AuditEvent]:
    """Read audit events from the audit_log table."""
    conditions = []
    params = []
    param_idx = 1
    
    if org_id is not None:
        conditions.append(f"org_id = ${param_idx}")
        params.append(org_id)
        param_idx += 1
    
    if actor is not None:
        conditions.append(f"actor = ${param_idx}")
        params.append(actor)
        param_idx += 1
    
    if action is not None:
        conditions.append(f"action = ${param_idx}")
        params.append(action)
        param_idx += 1
    
    if start_date is not None:
        conditions.append(f"timestamp >= ${param_idx}")
        params.append(start_date)
        param_idx += 1
    
    if end_date is not None:
        conditions.append(f"timestamp <= ${param_idx}")
        params.append(end_date)
        param_idx += 1
    
    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)
    
    sql = f"""
        SELECT id, timestamp, action, actor, org_id, target_server_id, outcome, metadata
        FROM audit_log
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])
    
    payload = {
        "sql": sql,
        "params": params,
    }
    
    response = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Query service error: {response.text}")
    
    result = response.json()
    rows = result.get("rows", [])
    
    return [
        AuditEvent(
            id=row["id"],
            timestamp=row["timestamp"],
            action=row["action"],
            actor=row["actor"],
            org_id=row["org_id"],
            target_server_id=row.get("target_server_id"),
            outcome=row["outcome"],
            metadata=row.get("metadata", {}),
        )
        for row in rows
    ]


if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    client = TestClient(app)
    
    written_rows = []
    
    def mock_post(url, json=None, timeout=None):
        mock_response = MagicMock()
        
        if url == WRITE_SERVICE_URL and json and json.get("table") == "audit_log":
            written_rows.extend(json.get("rows", []))
            
            timestamp = json["rows"][0]["timestamp"]
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "status": "success",
                "rows_written": len(json["rows"]),
                "last_insert_id": 42,
            }
        elif url == QUERY_SERVICE_URL:
            sql = json.get("sql", "")
            
            if "WHERE" in sql:
                filtered = [
                    r for r in written_rows
                    if (json.get("params", [])[0] == r["org_id"] if json.get("params") else True)
                ]
                rows_to_return = filtered
            else:
                rows_to_return = list(written_rows)
            
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "rows": [
                    {
                        "id": 42,
                        "timestamp": r["timestamp"],
                        "action": r["action"],
                        "actor": r["actor"],
                        "org_id": r["org_id"],
                        "target_server_id": r.get("target_server_id"),
                        "outcome": r["outcome"],
                        "metadata": r.get("metadata", {}),
                    }
                    for r in rows_to_return
                ]
            }
        else:
            mock_response.status_code = 404
            mock_response.json.return_value = {"error": "Not found"}
        
        return mock_response
    
    with patch("audit_log_api.requests.post", side_effect=mock_post):
        response = client.post(
            "/audit-log",
            json={
                "action": "verdict_override",
                "actor": "test@example.com",
                "org_id": "org_123",
                "target_server_id": "srv_456",
                "outcome": "success",
                "metadata": {"old_verdict": "approve", "new_verdict": "review"},
            },
        )
        
        assert response.status_code == 200, f"POST failed: {response.text}"
        data = response.json()
        assert data["id"] is not None, "id should not be null"
        assert data["action"] == "verdict_override"
        assert data["actor"] == "test@example.com"
        assert data["org_id"] == "org_123"
        
        response = client.get("/audit-log?org_id=org_123")
        assert response.status_code == 200, f"GET failed: {response.text}"
        events = response.json()
        assert isinstance(events, list), "Response should be a list"
        assert len(events) >= 1, "Should contain at least the written row"
        assert any(e["action"] == "verdict_override" for e in events), "Should contain written event"
    
    print("PASS: audit_log_api")