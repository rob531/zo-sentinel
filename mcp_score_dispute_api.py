"""mcp_score_dispute_api.py -- CRUD router for score dispute workflow.

Exposes per-server dispute endpoints: create, list, get, update (admin), withdraw.
Mounted via _OPTIONAL_ROUTERS in app/main.py (exports `router`).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/servers/{server_id}/disputes", tags=["disputes"])

# ===================== Stub auth: X-User-ID header =====================
_USER_ID_HEADER = os.getenv("USER_ID_HEADER", "X-User-ID")


def get_user_id(x_user_id: Optional[str] = Header(None, alias=_USER_ID_HEADER)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=400, detail=f"{_USER_ID_HEADER} header required")
    return x_user_id.strip()


# ===================== Pydantic models =====================
class CreateDispute(BaseModel):
    proposed_overall_risk: str
    proposed_axes: dict = {}
    reason_category: str
    explanation: str


class UpdateDispute(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None


class DisputeResponse(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Optional[dict] = None
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class DisputeListResponse(BaseModel):
    disputes: list[DisputeResponse]
    count: int


# ===================== Helpers =====================
def _dispute_to_response(d: McpScoreDispute) -> DisputeResponse:
    return DisputeResponse(
        id=d.id,
        server_id=d.server_id,
        submitted_by=d.submitted_by,
        proposed_overall_risk=d.proposed_overall_risk,
        proposed_axes=d.proposed_axes,
        reason_category=d.reason_category,
        explanation=d.explanation,
        status=d.status,
        admin_note=d.admin_note,
        created_at=d.created_at,
        resolved_at=d.resolved_at,
    )


# ===================== Endpoints =====================
@router.post("", response_model=DisputeResponse, status_code=201)
def create_dispute(
    server_id: str,
    payload: CreateDispute,
    db: Session = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> DisputeResponse:
    """Create a new dispute record for a server."""
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")
    dispute = McpScoreDispute(
        server_id=server_id,
        submitted_by=user_id,
        proposed_overall_risk=payload.proposed_overall_risk,
        proposed_axes=payload.proposed_axes,
        reason_category=payload.reason_category,
        explanation=payload.explanation,
        status="pending",
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return _dispute_to_response(dispute)


@router.get("", response_model=DisputeListResponse)
def list_disputes(
    server_id: str,
    db: Session = Depends(get_session),
) -> DisputeListResponse:
    """List all disputes for a server, newest first."""
    rows = db.execute(
        select(McpScoreDispute)
        .where(McpScoreDispute.server_id == server_id)
        .order_by(McpScoreDispute.created_at.desc())
    ).scalars().all()
    return DisputeListResponse(
        disputes=[_dispute_to_response(r) for r in rows],
        count=len(rows),
    )


@router.get("/{dispute_id}", response_model=DisputeResponse)
def get_dispute(
    server_id: str,
    dispute_id: int,
    db: Session = Depends(get_session),
) -> DisputeResponse:
    """Get a single dispute by id."""
    dispute = db.execute(
        select(McpScoreDispute).where(
            McpScoreDispute.id == dispute_id,
            McpScoreDispute.server_id == server_id,
        )
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found for server {server_id!r}")
    return _dispute_to_response(dispute)


@router.patch("/{dispute_id}", response_model=DisputeResponse)
def update_dispute(
    server_id: str,
    dispute_id: int,
    payload: UpdateDispute,
    db: Session = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> DisputeResponse:
    """Admin: update dispute status and/or add admin_note."""
    dispute = db.execute(
        select(McpScoreDispute).where(
            McpScoreDispute.id == dispute_id,
            McpScoreDispute.server_id == server_id,
        )
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found for server {server_id!r}")

    if payload.status is not None:
        dispute.status = payload.status
        # Auto-set resolved_at when transitioning to terminal states
        if payload.status in ("accepted", "rejected") and dispute.resolved_at is None:
            dispute.resolved_at = datetime.utcnow()

    if payload.admin_note is not None:
        dispute.admin_note = payload.admin_note

    db.commit()
    db.refresh(dispute)
    return _dispute_to_response(dispute)


@router.delete("/{dispute_id}", status_code=204)
def withdraw_dispute(
    server_id: str,
    dispute_id: int,
    db: Session = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> None:
    """Withdraw a pending dispute. Only the original submitter can withdraw, and only while pending."""
    dispute = db.execute(
        select(McpScoreDispute).where(
            McpScoreDispute.id == dispute_id,
            McpScoreDispute.server_id == server_id,
        )
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found for server {server_id!r}")

    if dispute.submitted_by != user_id:
        raise HTTPException(status_code=403, detail="Only the submitter can withdraw this dispute")

    if dispute.status != "pending":
        raise HTTPException(status_code=400, detail="Only pending disputes can be withdrawn")

    db.delete(dispute)
    db.commit()


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: one registry row so POST doesn't 404
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Test Server", url="https://example.com"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    c = TestClient(app)

    # POST creates dispute -> 201
    r = c.post("/servers/srv1/disputes",
               json={"proposed_overall_risk": "MEDIUM",
                     "proposed_axes": {"auth_strength": "STRONG"},
                     "reason_category": "axis_error",
                     "explanation": "Wrong auth label."},
               headers={"X-User-ID": "alice"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "pending"
    assert d["submitted_by"] == "alice"
    dispute_id = d["id"]

    # GET list includes it
    r = c.get("/servers/srv1/disputes")
    assert r.status_code == 200
    assert any(x["id"] == dispute_id for x in r.json()["disputes"]), r.json()

    # GET single returns full record
    r = c.get(f"/servers/srv1/disputes/{dispute_id}")
    assert r.status_code == 200
    assert r.json()["proposed_overall_risk"] == "MEDIUM"

    # PATCH by admin updates status + sets resolved_at
    r = c.patch(f"/servers/srv1/disputes/{dispute_id}",
                json={"status": "rejected", "admin_note": "Scores are accurate."},
                headers={"X-User-ID": "admin1"})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "rejected"
    assert j["resolved_at"] is not None

    # DELETE by non-owner -> 403
    r = c.post("/servers/srv1/disputes",
              json={"proposed_overall_risk": "LOW",
                    "proposed_axes": {},
                    "reason_category": "missing_context",
                    "explanation": "Test."},
              headers={"X-User-ID": "bob"})
    assert r.status_code == 201
    bob_id = r.json()["id"]
    r = c.delete(f"/servers/srv1/disputes/{bob_id}", headers={"X-User-ID": "carol"})
    assert r.status_code == 403, r.text

    # GET unknown server -> 404 (no disputes returned; server not found path hit in create)
    r = c.get("/servers/nosuchserver/disputes")
    assert r.status_code == 200  # list returns empty, server existence not enforced on list

    # POST to unknown server -> 404
    r = c.post("/servers/nosuchserver/disputes",
               json={"proposed_overall_risk": "LOW",
                     "proposed_axes": {},
                     "reason_category": "axis_error",
                     "explanation": "Test."},
               headers={"X-User-ID": "alice"})
    assert r.status_code == 404

    # Missing X-User-ID -> 400
    r = c.post("/servers/srv1/disputes",
               json={"proposed_overall_risk": "LOW",
                     "proposed_axes": {},
                     "reason_category": "axis_error",
                     "explanation": "Test."})
    assert r.status_code == 400

    print("PASS")
