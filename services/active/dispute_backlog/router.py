# deps: fastapi, pydantic, sqlalchemy
"""dispute_backlog -- unresolved score dispute backlog.

GET /api/disputes/backlog
  Returns count of unresolved disputes by status and the oldest unresolved
  dispute (by created_at ascending).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api", tags=["dispute_backlog"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class OldestDispute(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Optional[dict] = None
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class DisputeBacklogResponse(BaseModel):
    counts: Dict[str, int]
    oldest: Optional[OldestDispute] = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/disputes/backlog", response_model=DisputeBacklogResponse)
def get_backlog(
    db: Session = Depends(get_session),
) -> DisputeBacklogResponse:
    """
    Return a summary of unresolved disputes:
      - counts: how many disputes exist per status (excluding resolved)
      - oldest: the oldest unresolved dispute by created_at
    """
    # Count unresolved disputes grouped by status
    counts_stmt = (
        select(McpScoreDispute.status, func.count(McpScoreDispute.id))
        .where(McpScoreDispute.status != "resolved")
        .group_by(McpScoreDispute.status)
    )
    counts: Dict[str, int] = dict(db.execute(counts_stmt).all())

    # Fetch oldest unresolved dispute
    oldest_stmt = (
        select(McpScoreDispute)
        .where(McpScoreDispute.status != "resolved")
        .order_by(McpScoreDispute.created_at.asc())
        .limit(1)
    )
    oldest_row = db.execute(oldest_stmt).scalar_one_or_none()

    oldest: Optional[OldestDispute] = None
    if oldest_row is not None:
        oldest = OldestDispute.model_validate(oldest_row)

    return DisputeBacklogResponse(counts=counts, oldest=oldest)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    # Mock the app.db module so the import at the top of the file succeeds
    # when we run python3 router.py directly
    mock_db = MagicMock()
    mock_db.get_session = MagicMock()
    mock_models = MagicMock()
    mock_models.McpScoreDispute = MagicMock()

    sys.modules['app'] = MagicMock()
    sys.modules['app.db'] = mock_db
    sys.modules['app.models'] = mock_models

    # Re-import the module to pick up mocked deps (this works because
    # get_session was already resolved at import time above, so we just
    # need to ensure the test creates its own session factory)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool

    Base = declarative_base()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128) NOT NULL,
                submitted_by VARCHAR(128) NOT NULL,
                proposed_overall_risk VARCHAR(16) NOT NULL,
                proposed_axes TEXT,
                reason_category VARCHAR(48) NOT NULL,
                explanation TEXT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP NOT NULL,
                resolved_at TIMESTAMP
            )
        """))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    now = datetime.now(timezone.utc)
    rows = [
        ("srv_0", "user1@example.com", "LOW",    "pending",  now.replace(hour=10) - 3 * 86400),
        ("srv_1", "user2@example.com", "HIGH",   "pending",  now.replace(hour=10) - 5 * 86400),
        ("srv_2", "user3@example.com", "MEDIUM", "resolved", now.replace(hour=10) - 1 * 86400),
    ]
    with SessionLocal() as sess:
        for sid, sb, risk, st, ca in rows:
            sess.execute(
                text("""
                    INSERT INTO mcp_score_disputes
                        (server_id, submitted_by, proposed_overall_risk,
                         reason_category, explanation, status, created_at)
                    VALUES (:sid, :sb, :risk, :rcat, :exp, :st, :ca)
                """),
                {
                    "sid":   sid,
                    "sb":    sb,
                    "risk":  risk,
                    "rcat":  "incorrect_category",
                    "exp":   "Test explanation",
                    "st":    st,
                    "ca":    ca,
                },
            )
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/disputes/backlog")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "counts" in data
    assert data["counts"].get("pending") == 2, f"expected 2 pending, got {data['counts']}"
    assert "resolved" not in data["counts"]

    assert data["oldest"] is not None
    assert data["oldest"]["submitted_by"] == "user1@example.com", \
        f"expected user1, got {data['oldest']['submitted_by']}"
    assert data["oldest"]["status"] == "pending"

    print("PASS")
