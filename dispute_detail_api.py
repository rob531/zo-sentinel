"""dispute_detail_api.py -- Read-only detail router for score disputes.

Exposes GET /disputes/{dispute_id} and GET /disputes/{dispute_id}/axes.
Mounted via _OPTIONAL_ROUTERS in app/main.py (exports `router`).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/disputes", tags=["disputes"])

_USER_ID_HEADER = os.getenv("USER_ID_HEADER", "X-User-ID")


def get_user_id(x_user_id: Optional[str] = Header(None, alias=_USER_ID_HEADER)) -> str:
    if not x_user_id:
        raise HTTPException(status_code=400, detail=f"{_USER_ID_HEADER} header required")
    return x_user_id.strip()


# ===================== Pydantic models =====================
class DisputeDetailResponse(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str] = None
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Optional[dict] = None
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class AxisEntry(BaseModel):
    axis_name: str
    label: str


class DisputeAxesResponse(BaseModel):
    dispute_id: int
    proposed_overall_risk: str
    axes: list[AxisEntry]


# ===================== Helpers =====================
def _dispute_to_detail(d: McpScoreDispute, server_name: Optional[str] = None) -> DisputeDetailResponse:
    return DisputeDetailResponse(
        id=d.id,
        server_id=d.server_id,
        server_name=server_name,
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


def _extract_axes(proposed_axes: Optional[dict]) -> list[AxisEntry]:
    """Flatten proposed_axes JSON dict into axis label+value list."""
    if not proposed_axes:
        return []
    return [
        AxisEntry(axis_name=k, label=str(v))
        for k, v in proposed_axes.items()
        if k and v is not None
    ]


# ===================== Endpoints =====================
@router.get("/{dispute_id}", response_model=DisputeDetailResponse)
def get_dispute_detail(
    dispute_id: int,
    db: Session = Depends(get_session),
) -> DisputeDetailResponse:
    """Return the full detail record for a single dispute by ID, including
    server_name from the mcp_server_registry join."""
    dispute = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found")

    server_name: Optional[str] = None
    reg = db.get(McpServerRegistry, dispute.server_id)
    if reg is not None:
        server_name = reg.name

    return _dispute_to_detail(dispute, server_name=server_name)


@router.get("/{dispute_id}/axes", response_model=DisputeAxesResponse)
def get_dispute_axes(
    dispute_id: int,
    db: Session = Depends(get_session),
) -> DisputeAxesResponse:
    """Return list of proposed axis labels+values from the dispute's proposed_axes JSON."""
    dispute = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"Dispute {dispute_id} not found")

    return DisputeAxesResponse(
        dispute_id=dispute.id,
        proposed_overall_risk=dispute.proposed_overall_risk,
        axes=_extract_axes(dispute.proposed_axes),
    )


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

    # Seed: registry rows + two disputes (one open, one resolved)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    s.add(McpServerRegistry(server_id="srv2", name="Example Server",
                            url="https://example.com"))
    s.add(McpScoreDispute(
        id=1, server_id="srv1", submitted_by="alice",
        proposed_overall_risk="MEDIUM",
        proposed_axes={"overall_risk": "MEDIUM", "auth_strength": "STRONG",
                       "data_sensitivity": "LOW"},
        reason_category="axis_error", explanation="Wrong overall risk label.",
        status="pending"))
    s.add(McpScoreDispute(
        id=2, server_id="srv1", submitted_by="bob",
        proposed_overall_risk="LOW",
        proposed_axes={"overall_risk": "LOW", "auth_strength": "STRONG"},
        reason_category="missing_context", explanation="Consider context.",
        status="rejected", resolved_at=datetime(2025, 1, 15, 12, 0, 0)))
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

    # GET /disputes/1 returns open dispute with correct status + server_name
    r = c.get("/disputes/1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "pending", j
    assert j["server_name"] == "Stripe MCP", j
    assert j["proposed_overall_risk"] == "MEDIUM", j
    assert j["resolved_at"] is None, j

    # GET /disputes/2 returns resolved dispute with resolved_at non-null
    r = c.get("/disputes/2")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "rejected", j
    assert j["resolved_at"] is not None, j
    assert "2025-01-15" in j["resolved_at"], j

    # GET /disputes/999 returns 404
    r = c.get("/disputes/999")
    assert r.status_code == 404, r.text

    # GET /disputes/1/axes returns axis breakdown from proposed_axes
    r = c.get("/disputes/1/axes")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["dispute_id"] == 1, j
    assert j["proposed_overall_risk"] == "MEDIUM", j
    axes = j["axes"]
    assert len(axes) == 3, j
    axis_names = {a["axis_name"] for a in axes}
    assert "overall_risk" in axis_names, j
    assert "auth_strength" in axis_names, j
    assert "data_sensitivity" in axis_names, j

    # GET /disputes/2/axes
    r = c.get("/disputes/2/axes")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["axes"]) == 2, j

    # GET /disputes/999/axes returns 404
    r = c.get("/disputes/999/axes")
    assert r.status_code == 404, r.text

    print("PASS")
