# deps: fastapi, pydantic, sqlalchemy
"""score_dispute_audit -- audit summary of score disputes.

GET /api/disputes/audit?status={open|resolved|all}
  Returns total count, by-status breakdown, and recent dispute entries
  with server names (left-joined from mcp_server_registry).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_dispute_audit"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class DisputeAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: str
    server_name: Optional[str] = None
    proposed_overall_risk: Optional[str] = None
    reason_category: Optional[str] = None
    explanation: Optional[str] = None
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class DisputeStatusSummary(BaseModel):
    open: int = 0
    resolved: int = 0
    pending: int = 0


class DisputeAuditResponse(BaseModel):
    total: int
    by_status: DisputeStatusSummary
    recent: list[DisputeAuditEntry]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/disputes/audit", response_model=DisputeAuditResponse)
def get_disputes_audit(
    status: Annotated[
        Literal["open", "resolved", "pending", "all"],
        Query(description="Filter by dispute status"),
    ] = "all",
    db: Session = Depends(get_session),
) -> DisputeAuditResponse:
    """
    Return an audit summary of score disputes:
      - total: count of all matching disputes
      - by_status: counts for open / resolved / pending
      - recent: up to 20 recent dispute entries with server names
    """
    # Base filter conditions
    base_filter = []
    if status != "all":
        base_filter.append(McpScoreDispute.status == status)

    # Count queries per status bucket
    total = db.scalar(
        select(func.count(McpScoreDispute.id)).where(*base_filter)
    ) or 0

    open_count = (
        db.scalar(
            select(func.count(McpScoreDispute.id)).where(
                McpScoreDispute.status == "open"
            )
        ) or 0
    )
    resolved_count = (
        db.scalar(
            select(func.count(McpScoreDispute.id)).where(
                McpScoreDispute.status == "resolved"
            )
        ) or 0
    )
    pending_count = (
        db.scalar(
            select(func.count(McpScoreDispute.id)).where(
                McpScoreDispute.status == "pending"
            )
        ) or 0
    )

    # Recent disputes with server name (LEFT JOIN via subquery)
    recent_stmt = (
        select(
            McpScoreDispute,
            McpServerRegistry.name.label("server_name"),
        )
        .outerjoin(
            McpServerRegistry,
            McpScoreDispute.server_id == McpServerRegistry.server_id,
        )
        .where(*base_filter)
        .order_by(McpScoreDispute.created_at.desc())
        .limit(20)
    )
    recent_rows = db.execute(recent_stmt).all()

    recent = []
    for row in recent_rows:
        dispute = row[0]
        server_name = row[1]
        entry = DisputeAuditEntry.model_validate(dispute)
        entry.server_name = server_name
        recent.append(entry)

    return DisputeAuditResponse(
        total=total,
        by_status=DisputeStatusSummary(
            open=open_count,
            resolved=resolved_count,
            pending=pending_count,
        ),
        recent=recent,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    # Stub app modules so top-level imports resolve at test time
    mock_db = MagicMock()
    mock_db.get_session = MagicMock()
    mock_models = MagicMock()
    mock_models.McpScoreDispute = MagicMock()
    mock_models.McpServerRegistry = MagicMock()
    sys.modules["app"] = MagicMock()
    sys.modules["app.db"] = mock_db
    sys.modules["app.models"] = mock_models

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
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(256)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128) NOT NULL,
                submitted_by VARCHAR(128) NOT NULL,
                proposed_overall_risk VARCHAR(16),
                proposed_axes TEXT,
                reason_category VARCHAR(48),
                explanation TEXT,
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

    now = datetime.utcnow()
    with SessionLocal() as sess:
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_alpha", "name": "Alpha Server"},
        )
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_beta", "name": "Beta Server"},
        )
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_gamma", "name": "Gamma Server"},
        )
        disputes = [
            ("srv_alpha", "alice@example.com", "LOW",    "open",     now.replace(hour=10) - 3 * 86400),
            ("srv_beta",  "bob@example.com",   "HIGH",   "open",     now.replace(hour=10) - 5 * 86400),
            ("srv_gamma", "carol@example.com", "MEDIUM", "resolved", now.replace(hour=10) - 1 * 86400),
        ]
        for sid, sb, risk, st, ca in disputes:
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

    # Test all
    resp = client.get("/api/disputes/audit?status=all")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3, f"expected 3, got {data['total']}"
    assert data["by_status"]["open"] == 2, f"expected 2 open, got {data['by_status']}"
    assert data["by_status"]["resolved"] == 1, f"expected 1 resolved, got {data['by_status']}"
    assert len(data["recent"]) == 3

    # Verify server_name join worked
    names = {e["server_name"] for e in data["recent"]}
    assert "Alpha Server" in names

    # Test status filter
    resp2 = client.get("/api/disputes/audit?status=open")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 2
    assert len(data2["recent"]) == 2
    for e in data2["recent"]:
        assert e["status"] == "open", f"expected open, got {e['status']}"

    print("PASS")
