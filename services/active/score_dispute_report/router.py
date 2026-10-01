# deps: fastapi, pydantic, sqlalchemy
"""score_dispute_report -- public API for querying and submitting score disputes.

GET  /api/score/dispute          List disputes (optional server_id/status filter).
GET  /api/score/dispute/{id}     Get single dispute by id.
POST /api/score/dispute          Submit a new dispute.

Auth: public.
Data: app tier via get_session + McpScoreDispute + McpServerRegistry.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_dispute_report"])


# --------------------------------------------------------------------------- #
# Request / response shapes
# --------------------------------------------------------------------------- #

class DisputeBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Optional[dict] = None
    reason_category: str
    explanation: str


class DisputeCreate(DisputeBase):
    pass


class DisputeResponse(DisputeBase):
    id: int
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class DisputeWithServer(DisputeResponse):
    server_name: Optional[str] = None


class DisputeListResponse(BaseModel):
    items: list[DisputeWithServer]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _resolve_server_name(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    return row


def _dispute_with_name(db: Session, dispute: McpScoreDispute) -> DisputeWithServer:
    entry = DisputeWithServer.model_validate(dispute)
    entry.server_name = _resolve_server_name(db, dispute.server_id)
    return entry


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/score/dispute", response_model=DisputeListResponse)
def list_disputes(
    db: Session = Depends(get_session),
    server_id: Optional[str] = Query(None, description="Filter by server_id"),
    dispute_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DisputeListResponse:
    """List disputes, optionally filtered by server_id and/or status. Ordered by created_at desc."""
    stmt = select(McpScoreDispute)
    if server_id:
        stmt = stmt.where(McpScoreDispute.server_id == server_id)
    if dispute_status:
        stmt = stmt.where(McpScoreDispute.status == dispute_status)
    stmt = stmt.order_by(desc(McpScoreDispute.created_at)).limit(limit).offset(offset)

    count_stmt = select(func.count(McpScoreDispute.id))
    if server_id:
        count_stmt = count_stmt.where(McpScoreDispute.server_id == server_id)
    if dispute_status:
        count_stmt = count_stmt.where(McpScoreDispute.status == dispute_status)
    total = db.scalar(count_stmt) or 0

    rows = db.execute(stmt).scalars().all()
    items = [_dispute_with_name(db, d) for d in rows]
    return DisputeListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/score/dispute/{dispute_id}", response_model=DisputeWithServer)
def get_dispute(dispute_id: int, db: Session = Depends(get_session)) -> DisputeWithServer:
    """Return a single dispute by id, or 404."""
    dispute = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispute not found")
    return _dispute_with_name(db, dispute)


@router.post("/score/dispute", response_model=DisputeResponse, status_code=status.HTTP_201_CREATED)
def create_dispute(data: DisputeCreate, db: Session = Depends(get_session)) -> DisputeResponse:
    """Submit a new score dispute. All fields required; explanation is free-text."""
    now = datetime.now(timezone.utc)
    dispute = McpScoreDispute(
        server_id=data.server_id,
        submitted_by=data.submitted_by,
        proposed_overall_risk=data.proposed_overall_risk,
        proposed_axes=data.proposed_axes,
        reason_category=data.reason_category,
        explanation=data.explanation,
        status="pending",
        created_at=now,
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return DisputeResponse.model_validate(dispute)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

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

    def _override():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    with SessionLocal() as sess:
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_a", "name": "Server Alpha"},
        )
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_b", "name": "Server Beta"},
        )
        # pending dispute
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, 'pending', :ca)
        """), {"sid": "srv_a", "sb": "user_1", "risk": "LOW",
               "cat": "incorrect_category", "exp": "Should be LOW not MEDIUM",
               "ca": now - timedelta(days=3)})
        # open dispute
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, 'open', :ca)
        """), {"sid": "srv_a", "sb": "user_2", "risk": "HIGH",
               "cat": "outdated_score", "exp": "Stale score", "ca": now - timedelta(days=1)})
        # resolved dispute
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, admin_note, created_at, resolved_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, 'resolved', :note, :ca, :ra)
        """), {"sid": "srv_b", "sb": "user_1", "risk": "LOW",
               "cat": "missing_data", "exp": "Missing axis data",
               "note": "Approved", "ca": now - timedelta(days=5),
               "ra": now - timedelta(days=4)})
        sess.commit()

    client = TestClient(app)

    # POST -- creates a new pending dispute
    r = client.post("/api/score/dispute", json={
        "server_id": "srv_a",
        "submitted_by": "user_3",
        "proposed_overall_risk": "MEDIUM",
        "proposed_axes": {"auth_strength": "WEAK"},
        "reason_category": "incorrect_category",
        "explanation": "New evidence",
    })
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # GET list
    r = client.get("/api/score/dispute")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 4, f"total={data['total']}"
    assert len(data["items"]) == 4

    # GET list filtered by server
    r = client.get("/api/score/dispute?server_id=srv_a")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3, f"server total={data['total']}"

    # GET list filtered by status=pending (2: the original + the POST)
    r = client.get("/api/score/dispute?status=pending")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2, f"pending total={data['total']}"

    # GET by id
    r = client.get(f"/api/score/dispute/{new_id}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["server_id"] == "srv_a"
    assert d["status"] == "pending"
    assert d["server_name"] == "Server Alpha"

    # GET 404
    r = client.get("/api/score/dispute/99999")
    assert r.status_code == 404

    print("PASS")
