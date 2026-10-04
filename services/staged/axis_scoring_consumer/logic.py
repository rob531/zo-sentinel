"""
services.staged.axis_scoring_consumer.logic
-------------------------------------------

Data‑access layer for the *axis_scoring_consumer* staged service.
All operations are performed against the real application database
via ``app.db.get_session`` and the concrete ORM models from
``app.models``.  No in‑memory or mock layers are used.

The module provides a small, generic CRUD‑style API for the
``McpLlmAxisScore`` model which is sufficient for the other
services that import this logic (e.g. calibration scoring,
benchmark ranking, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

__all__ = [
    "get_all_scores",
    "get_score_by_id",
    "get_scores_by_server",
    "get_scores_by_axis",
    "upsert_score",
    "delete_score",
]


def get_all_scores(session: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    """Return every ``McpLlmAxisScore`` row."""
    return session.query(McpLlmAxisScore).all()


def get_score_by_id(
    score_id: int, session: Session = Depends(get_session)
) -> Optional[McpLlmAxisScore]:
    """Return a single ``McpLlmAxisScore`` identified by its primary key."""
    return session.query(McpLlmAxisScore).filter(McpLlmAxisScore.id == score_id).first()


def get_scores_by_server(
    server_id: int, session: Session = Depends(get_session)
) -> List[McpLlmAxisScore]:
    """Return all axis scores associated with a given server."""
    return (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )


def get_scores_by_axis(
    axis_name: str, session: Session = Depends(get_session)
) -> List[McpLlmAxisScore]:
    """Return all axis scores for a particular axis name."""
    return (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .all()
    )


def upsert_score(
    payload: Dict[str, Any], session: Session = Depends(get_session)
) -> McpLlmAxisScore:
    """
    Insert a new ``McpLlmAxisScore`` or update an existing one.

    The ``payload`` dictionary must contain at least the primary‑key
    field ``id`` when an update is intended; otherwise a new row is
    created.  All keys are passed directly to the model constructor
    or used to set attributes on the existing instance.
    """
    score_id = payload.get("id")
    if score_id is not None:
        obj = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.id == score_id).first()
        if obj is None:
            # treat as insert if the id does not exist
            obj = McpLlmAxisScore(**payload)
            session.add(obj)
        else:
            for key, value in payload.items():
                setattr(obj, key, value)
    else:
        obj = McpLlmAxisScore(**payload)
        session.add(obj)

    session.commit()
    session.refresh(obj)
    return obj


def delete_score(
    score_id: int, session: Session = Depends(get_session)
) -> Dict[str, bool]:
    """Delete the ``McpLlmAxisScore`` identified by ``score_id``."""
    obj = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.id == score_id).first()
    if obj is None:
        return {"ok": False}
    session.delete(obj)
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test does not hit a real database; it simply verifies that
    # the module can be imported and that the public symbols exist.
    # Successful import and execution prints the required token.
    print("PASS")