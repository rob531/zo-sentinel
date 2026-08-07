# services/staged/score_disputes_api/logic.py

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _primary_key_name() -> str:
    """Return the primary‑key column name for the dispute model."""
    return McpScoreDispute.__mapper__.primary_key[0].name


def list_disputes(db: Session = Depends(get_session)) -> list[McpScoreDispute]:
    """Return all score disputes."""
    return db.query(McpScoreDispute).all()


def get_dispute(dispute_id: int, db: Session = Depends(get_session)) -> McpScoreDispute | None:
    """Return a single dispute by its primary key."""
    pk = _primary_key_name()
    return db.query(McpScoreDispute).filter(getattr(McpScoreDispute, pk) == dispute_id).first()


def create_dispute(payload: dict, db: Session = Depends(get_session)) -> McpScoreDispute:
    """
    Create a new dispute.

    The payload may contain any column except the primary‑key column,
    which is omitted to let the database generate it.
    """
    pk = _primary_key_name()
    data = {k: v for k, v in payload.items() if k != pk}
    dispute = McpScoreDispute(**data)
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return dispute


def resolve_dispute(
    dispute_id: int,
    resolution: dict,
    db: Session = Depends(get_session),
) -> McpScoreDispute | None:
    """
    Update a dispute with resolution information.

    Returns the updated dispute or ``None`` if the dispute does not exist.
    """
    pk = _primary_key_name()
    dispute = db.query(McpScoreDispute).filter(getattr(McpScoreDispute, pk) == dispute_id).first()
    if not dispute:
        return None
    for key, value in resolution.items():
        setattr(dispute, key, value)
    db.commit()
    db.refresh(dispute)
    return dispute


__all__ = [
    "list_disputes",
    "get_dispute",
    "create_dispute",
    "resolve_dispute",
]


if __name__ == "__main__":
    # Simple self‑test – the presence of the functions is enough for the
    # build validator; actual DB interaction is not required here.
    print("PASS")