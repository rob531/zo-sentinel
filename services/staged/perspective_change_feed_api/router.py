"""Logic layer for perspective change feed API."""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from pydantic import BaseModel


class PerspectiveEventResponse(BaseModel):
    """Response model for perspective events."""
    id: str
    perspective_id: str
    server_id: str
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    created_at: datetime


def get_perspective_feed(
    session: Session,
    perspective_id: Optional[str] = None,
    limit: int = 50,
    since: Optional[datetime] = None
) -> List[PerspectiveEventResponse]:
    """
    Fetch perspective events feed with optional filtering.
    
    Args:
        session: Database session
        perspective_id: Optional filter by perspective ID
        limit: Maximum number of events to return
        since: Optional ISO8601 datetime to filter events after
        
    Returns:
        List of perspective events with server details
    """
    query = text("""
        SELECT 
            pe.id,
            pe.perspective_id,
            pe.server_id,
            msr.server_name,
            pe.change_type,
            pe.old_tier,
            pe.new_tier,
            pe.created_at
        FROM perspective_events pe
        JOIN perspectives p ON pe.perspective_id = p.id
        JOIN mcp_server_registry msr ON pe.server_id = msr.id
        WHERE (:perspective_id IS NULL OR pe.perspective_id = :perspective_id)
          AND (:since IS NULL OR pe.created_at >= :since)
        ORDER BY pe.created_at DESC
        LIMIT :limit
    """)
    
    result = session.execute(query, {
        "perspective_id": perspective_id,
        "since": since,
        "limit": limit
    })
    
    rows = result.fetchall()
    return [
        PerspectiveEventResponse(
            id=str(row.id),
            perspective_id=str(row.perspective_id),
            server_id=str(row.server_id),
            server_name=row.server_name,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            created_at=row.created_at
        )
        for row in rows
    ]