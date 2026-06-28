"""score_dispute_api.py -- user-submitted score disputes / proposed re-scores.

Authenticated users dispute an MCP server's risk: they pick the overall risk they
believe is correct, choose a structured reason category, write a (required) short
explanation, and optionally propose new labels for any of the 6 sub-axes. Admins
review and resolve (approve/reject) -- record-only for now; a later job may consume
approved disputes into score overrides.

Mounted by app.main via _OPTIONAL_ROUTERS. Reuses the real data layer (app.db,
app.models) and the existing Clerk auth (get_principal/require_admin from
verdict_breakdown_api) -- no new auth, no inline models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
# Reuse the existing Clerk auth + principal (defined in the exemplar) -- do not redefine auth.
from verdict_breakdown_api import get_principal, require_admin, Principal

router = APIRouter(prefix="/api", tags=["disputes"])

RISK_CLASSES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
REASON_CATEGORIES = (
    "official_or_established_maintainer",
    "false_positive_overrated",
    "underrated_actual_risk",
    "outdated_assessment",
    "incorrect_capability_or_axis",
    "other",
)
AXES = ("auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class DisputeIn(BaseModel):
    server_id: str
    proposed_overall_risk: str
    reason_category: str
    explanation: str
    proposed_axes: Optional[Dict[str, str]] = None


class ResolveIn(BaseModel):
    decision: str
    admin_note: Optional[str] = None


def _dispute_dict(d: McpScoreDispute) -> dict:
    return {
        "id": d.id, "server_id": d.server_id, "submitted_by": d.submitted_by,
        "proposed_overall_risk": d.proposed_overall_risk,
        "proposed_axes": d.proposed_axes, "reason_category": d.reason_category,
        "explanation": d.explanation, "status": d.status,
        "admin_note": d.admin_note,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
    }


@router.post("/disputes")
def submit_dispute(payload: DisputeIn, db: Session = Depends(get_session),
                   principal: Principal = Depends(get_principal)) -> dict:
    """Submit a dispute / proposed re-score for a server (auth required)."""
    if payload.proposed_overall_risk not in RISK_CLASSES:
        raise HTTPException(status_code=400,
                            detail=f"proposed_overall_risk must be one of {list(RISK_CLASSES)}")
    if payload.reason_category not in REASON_CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"reason_category must be one of {list(REASON_CATEGORIES)}")
    if len((payload.explanation or "").strip()) < 10:
        raise HTTPException(status_code=400,
                            detail="explanation is required (at least 10 characters)")
    if payload.proposed_axes:
        for ax, label in payload.proposed_axes.items():
            if ax not in AXES:
                raise HTTPException(status_code=400,
                                    detail=f"unknown axis '{ax}'; valid axes: {list(AXES)}")
            if not isinstance(label, str) or not label.strip():
                raise HTTPException(status_code=400,
                                    detail=f"axis '{ax}' needs a non-empty label")
    if db.get(McpServerRegistry, payload.server_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown server_id {payload.server_id!r}")

    row = McpScoreDispute(
        server_id=payload.server_id, submitted_by=principal.user_id,
        proposed_overall_risk=payload.proposed_overall_risk,
        proposed_axes=payload.proposed_axes, reason_category=payload.reason_category,
        explanation=payload.explanation.strip(), status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "submitted", "id": row.id}


@router.get("/disputes/mine")
def my_disputes(db: Session = Depends(get_session),
                principal: Principal = Depends(get_principal)) -> dict:
    """The caller's own disputes, newest first."""
    rows = db.execute(
        select(McpScoreDispute)
        .where(McpScoreDispute.submitted_by == principal.user_id)
        .order_by(McpScoreDispute.created_at.desc())
    ).scalars().all()
    return {"disputes": [_dispute_dict(d) for d in rows], "count": len(rows)}


@router.get("/admin/disputes")
def admin_list_disputes(status: str = Query("pending"),
                        db: Session = Depends(get_session),
                        principal: Principal = Depends(require_admin)) -> dict:
    """Admin review queue: disputes filtered by status, newest first."""
    rows = db.execute(
        select(McpScoreDispute)
        .where(McpScoreDispute.status == status)
        .order_by(McpScoreDispute.created_at.desc())
    ).scalars().all()
    return {"disputes": [_dispute_dict(d) for d in rows], "count": len(rows), "status": status}


@router.post("/admin/disputes/{dispute_id}")
def resolve_dispute(dispute_id: int, payload: ResolveIn,
                    db: Session = Depends(get_session),
                    principal: Principal = Depends(require_admin)) -> dict:
    """Admin resolve: approve or reject. Record-only -- does not mutate scores."""
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    row = db.get(McpScoreDispute, dispute_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"dispute {dispute_id} not found")
    row.status = payload.decision
    row.admin_note = payload.admin_note
    row.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "resolved", "id": dispute_id, "decision": payload.decision}


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
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
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Test MCP",
                            url="https://github.com/x/y", registry_source="glama"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="u1", role="public")
    app.dependency_overrides[require_admin] = lambda: Principal(user_id="admin", role="admin")
    c = TestClient(app)

    ok = c.post("/api/disputes", json={"server_id": "srv1", "proposed_overall_risk": "MEDIUM",
                "reason_category": "false_positive_overrated",
                "explanation": "Official maintainer; not actually high risk",
                "proposed_axes": {"maintainer_trust": "ESTABLISHED"}})
    assert ok.status_code == 200, ok.text
    did = ok.json()["id"]
    assert c.post("/api/disputes", json={"server_id": "srv1", "proposed_overall_risk": "BOGUS",
                  "reason_category": "other", "explanation": "x" * 10}).status_code == 400
    assert c.post("/api/disputes", json={"server_id": "srv1", "proposed_overall_risk": "LOW",
                  "reason_category": "other", "explanation": "short"}).status_code == 400
    assert c.post("/api/disputes", json={"server_id": "nope", "proposed_overall_risk": "LOW",
                  "reason_category": "other", "explanation": "valid explanation here"}).status_code == 404
    lst = c.get("/api/admin/disputes?status=pending").json()
    assert any(d["id"] == did for d in lst["disputes"]), lst
    res = c.post(f"/api/admin/disputes/{did}", json={"decision": "approved"})
    assert res.status_code == 200 and res.json()["decision"] == "approved", res.text
    assert c.get("/api/disputes/mine").json()["count"] >= 1
    print("PASS")
