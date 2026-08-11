"""
services.staged.verdict_watchlist.contract
-----------------------------------------

FastAPI contract for the ``verdict_watchlist`` service.

Provides:
    * GET    /api/watchlist               – list all watch‑list entries
    * POST   /api/watchlist               – add a new watch‑list entry
    * DELETE /api/watchlist/{server_id}  – remove a watch‑list entry
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Real data‑layer imports – must be used by the service implementation.
# --------------------------------------------------------------------------- #
from app.db import Base, get_session
from app.models import McpServerRegistry  # type: ignore[attr-defined]

# --------------------------------------------------------------------------- #
# Helper – convert a SQLAlchemy model instance to a plain dict.
# --------------------------------------------------------------------------- #
def _model_to_dict(instance: Any) -> Dict[str, Any]:
    data = dict(instance.__dict__)  # shallow copy
    data.pop("_sa_instance_state", None)
    return data


# --------------------------------------------------------------------------- #
# Router definition.
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("/", response_model=List[Dict[str, Any]])
def list_watchlist(db: Session = Depends(get_session)):
    """Return all watch‑list entries."""
    entries = db.query(McpServerRegistry).all()
    return [_model_to_dict(e) for e in entries]


@router.post(
    "/",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def add_watchlist(entry: Dict[str, Any], db: Session = Depends(get_session)):
    """Create a new watch‑list entry."""
    try:
        obj = McpServerRegistry(**entry)
    except TypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _model_to_dict(obj)


@router.delete(
    "/{server_id}",
    response_model=Dict[str, str],
    status_code=status.HTTP_200_OK,
)
def delete_watchlist(server_id: int, db: Session = Depends(get_session)):
    """Delete a watch‑list entry by its primary‑key ``id``."""
    obj = db.query(McpServerRegistry).filter_by(id=server_id).first()
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with id {server_id} not found",
        )
    db.delete(obj)
    db.commit()
    return {"detail": "deleted"}


# --------------------------------------------------------------------------- #
# FastAPI application – used both by the real service and the self‑test.
# --------------------------------------------------------------------------- #
app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.verdict_watchlist.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ----------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine that mimics the real DB for testing.
    # ----------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Dependency override to inject the test session.
    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ----------------------------------------------------------------------- #
    # Execute a minimal acceptance scenario.
    # ----------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/watchlist/")
    if resp.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        sys.exit(1)