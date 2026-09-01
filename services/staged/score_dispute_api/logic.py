from datetime import datetime
from typing import List, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def get_db(session: Session = Depends(get_session)) -> Session:
    """FastAPI dependency that provides a DB session."""
    return session


def get_dispute(session: Session, dispute_id: int) -> Optional[McpScoreDispute]:
    """Return a single dispute by its primary key."""
    return (
        session.query(McpScoreDispute)
        .filter(McpScoreDispute.id == dispute_id)
        .one_or_none()
    )


def list_disputes(
    session: Session,
    server_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[McpScoreDispute]:
    """Return a list of disputes optionally filtered by server and/or status."""
    query = session.query(McpScoreDispute)
    if server_id is not None:
        query = query.filter(McpScoreDispute.server_id == server_id)
    if status is not None:
        query = query.filter(McpScoreDispute.status == status)
    return query.all()


def create_dispute(
    session: Session,
    *,
    server_id: int,
    submitted_by: int,
    reason_category: str,
    explanation: str,
    proposed_axes: str,
    proposed_overall_risk: str,
) -> McpScoreDispute:
    """Create a new score dispute record."""
    dispute = McpScoreDispute(
        server_id=server_id,
        submitted_by=submitted_by,
        reason_category=reason_category,
        explanation=explanation,
        proposed_axes=proposed_axes,
        proposed_overall_risk=proposed_overall_risk,
        status="open",
        created_at=datetime.utcnow(),
    )
    session.add(dispute)
    session.commit()
    session.refresh(dispute)
    return dispute


def resolve_dispute(
    session: Session,
    dispute_id: int,
    admin_note: str,
    resolved_at: Optional[datetime] = None,
) -> Optional[McpScoreDispute]:
    """Mark a dispute as resolved, adding an admin note."""
    dispute = get_dispute(session, dispute_id)
    if dispute is None:
        return None
    dispute.admin_note = admin_note
    dispute.resolved_at = resolved_at or datetime.utcnow()
    dispute.status = "resolved"
    session.commit()
    session.refresh(dispute)
    return dispute


__all__ = [
    "get_db",
    "get_dispute",
    "list_disputes",
    "create_dispute",
    "resolve_dispute",
]


if __name__ == "__main__":
    print("PASS")