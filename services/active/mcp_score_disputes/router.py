# deps: fastapi, pydantic, sqlalchemy
"""mcp_score_disputes -- public intake and query API for user-submitted score disputes.

GET  /api/disputes                        List disputes (paginated, filterable).
GET  /api/disputes/{dispute_id}            Get a single dispute by id.
POST /api/disputes                        Submit a new dispute.
GET  /api/disputes/by-server/{server_id}  List disputes for a server.
GET  /api/disputes/by-submitter/{submitted_by}  List disputes by submitter.

Auth: public.
Data: app tier via get_session + McpScoreDispute + McpServerRegistry.
"""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_score_disputes"])


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

@router.get("/disputes", response_model=DisputeListResponse)
def list_disputes(
    db: Session = Depends(get_session),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DisputeListResponse:
    """List all disputes, optionally filtered by status. Ordered by created_at desc."""
    stmt = select(McpScoreDispute)
    if status_filter:
        stmt = stmt.where(McpScoreDispute.status == status_filter)
    stmt = stmt.order_by(desc(McpScoreDispute.created_at)).limit(limit).offset(offset)

    if status_filter:
        total = db.scalar(
            select(func.count(McpScoreDispute.id)).where(McpScoreDispute.status == status_filter)
        ) or 0
    else:
        total = db.scalar(select(func.count(McpScoreDispute.id))) or 0

    rows = db.execute(stmt).scalars().all()
    items = [_dispute_with_name(db, d) for d in rows]
    return DisputeListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/disputes/{dispute_id}", response_model=DisputeWithServer)
def get_dispute(dispute_id: int, db: Session = Depends(get_session)) -> DisputeWithServer:
    """Return a single dispute by id, or 404."""
    dispute = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispute not found")
    return _dispute_with_name(db, dispute)


@router.post("/disputes", response_model=DisputeResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/disputes/by-server/{server_id}", response_model=DisputeListResponse)
def by_server(
    server_id: str,
    db: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DisputeListResponse:
    """Return all disputes for a given server_id."""
    base_stmt = select(McpScoreDispute).where(McpScoreDispute.server_id == server_id)
    total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    rows = db.execute(
        base_stmt.order_by(desc(McpScoreDispute.created_at)).limit(limit).offset(offset)
    ).scalars().all()
    items = [_dispute_with_name(db, d) for d in rows]
    return DisputeListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/disputes/by-submitter/{submitted_by}", response_model=DisputeListResponse)
def by_submitter(
    submitted_by: str,
    db: Session = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DisputeListResponse:
    """Return all disputes submitted by a given user/submitter."""
    base_stmt = select(McpScoreDispute).where(McpScoreDispute.submitted_by == submitted_by)
    total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
    rows = db.execute(
        base_stmt.order_by(desc(McpScoreDispute.created_at)).limit(limit).offset(offset)
    ).scalars().all()
    items = [_dispute_with_name(db, d) for d in rows]
    return DisputeListResponse(items=items, total=total, limit=limit, offset=offset)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta

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

    now = datetime.now(timezone.utc)

    with SessionLocal() as sess:
        sess.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"
        ), {"sid": "srv_a", "name": "Server Alpha"})
        sess.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"
        ), {"sid": "srv_b", "name": "Server Beta"})
        # 1 seed record with status=pending
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, 'pending', :ca)
        """), {"sid": "srv_a", "sb": "user_1", "risk": "LOW",
               "cat": "incorrect_category", "exp": "Should be LOW not MEDIUM",
               "ca": now - timedelta(days=3)})
        # 1 seed record with status=open
        sess.execute(text("""
            INSERT INTO mcp_score_disputes
                (server_id, submitted_by, proposed_overall_risk, reason_category,
                 explanation, status, created_at)
            VALUES (:sid, :sb, :risk, :cat, :exp, 'open', :ca)
        """), {"sid": "srv_a", "sb": "user_2", "risk": "HIGH",
               "cat": "outdated_score", "exp": "Stale score", "ca": now - timedelta(days=1)})
        # 1 seed record with status=resolved
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

    # POST creates a new dispute with status=pending
    r = client.post("/api/disputes", json={
        "server_id": "srv_a",
        "submitted_by": "user_3",
        "proposed_overall_risk": "MEDIUM",
        "proposed_axes": {"auth_strength": "WEAK"},
        "reason_category": "incorrect_category",
        "explanation": "New evidence",
    })
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # GET list: 3 seed + 1 POSTed = 4
    r = client.get("/api/disputes")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 4, f"total={data['total']}"
    assert len(data["items"]) == 4

    # GET list filtered: 1 pending seed + 1 POSTed = 2 pending
    r = client.get("/api/disputes?status=pending")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2, f"pending total={data['total']}"

    # GET by id
    r = client.get(f"/api/disputes/{new_id}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["server_id"] == "srv_a"
    assert d["status"] == "pending"
    assert d["server_name"] == "Server Alpha"

    # GET 404
    r = client.get("/api/disputes/99999")
    assert r.status_code == 404

    # GET by server: 2 seed for srv_a + 1 POSTed for srv_a = 3
    r = client.get("/api/disputes/by-server/srv_a")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3, f"by-server total={data['total']}"

    # GET by submitter: 1 seed for user_1 + 1 for user_1 (resolved) = 2
    r = client.get("/api/disputes/by-submitter/user_1")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2, f"by-submitter total={data['total']}"

    print("PASS")
