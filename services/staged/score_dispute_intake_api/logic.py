from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, constr
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


RiskTier = Literal[
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
]

ALLOWED_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class AxisProposal(BaseModel):
    label: str
    p_top: float


class DisputeCreate(BaseModel):
    server_id: str
    submitted_by: str
    proposed_overall_risk: RiskTier
    proposed_axes: Dict[str, AxisProposal]
    reason_category: str
    explanation: constr(strip_whitespace=True, min_length=1)


class DisputeCreatedResponse(BaseModel):
    id: int
    server_id: str
    status: str
    created_at: Optional[object]


class DisputeResponse(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Dict[str, AxisProposal]
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: Optional[object]
    resolved_at: Optional[object]


router = APIRouter(prefix="/api", tags=["disputes"])


@router.post("/disputes", response_model=DisputeCreatedResponse, status_code=201)
def create_dispute(payload: DisputeCreate, session: Session = Depends(get_session)):
    invalid_axes = sorted(set(payload.proposed_axes) - ALLOWED_AXES)
    if invalid_axes:
        raise HTTPException(
            status_code=422,
            detail=f"invalid axis names: {', '.join(invalid_axes)}",
        )

    registry_row = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
        )
        .filter(McpServerRegistry.server_id == payload.server_id)
        .first()
    )
    if registry_row is None:
        raise HTTPException(status_code=404, detail="not found")

    dispute = McpScoreDispute(
        server_id=payload.server_id,
        submitted_by=payload.submitted_by,
        proposed_overall_risk=payload.proposed_overall_risk,
        proposed_axes={
            axis_name: proposal.model_dump()
            if hasattr(proposal, "model_dump")
            else proposal.dict()
            for axis_name, proposal in payload.proposed_axes.items()
        },
        reason_category=payload.reason_category,
        explanation=payload.explanation,
        status="open",
        admin_note=None,
        resolved_at=None,
    )
    session.add(dispute)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(dispute)

    return {
        "id": dispute.id,
        "server_id": dispute.server_id,
        "status": dispute.status,
        "created_at": dispute.created_at,
    }


@router.get("/disputes/{dispute_id}", response_model=DisputeResponse)
def get_dispute(dispute_id: int, session: Session = Depends(get_session)):
    dispute = session.get(McpScoreDispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="not found")

    return {
        "id": dispute.id,
        "server_id": dispute.server_id,
        "submitted_by": dispute.submitted_by,
        "proposed_overall_risk": dispute.proposed_overall_risk,
        "proposed_axes": dispute.proposed_axes,
        "reason_category": dispute.reason_category,
        "explanation": dispute.explanation,
        "status": dispute.status,
        "admin_note": dispute.admin_note,
        "created_at": dispute.created_at,
        "resolved_at": dispute.resolved_at,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add(
            McpServerRegistry(
                server_id="server-test-1",
                risk_tier="TRUSTED_GENERAL",
            )
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        create_response = client.post(
            "/api/disputes",
            json={
                "server_id": "server-test-1",
                "submitted_by": "analyst@example.com",
                "proposed_overall_risk": "CAUTION_LIMITED",
                "proposed_axes": {
                    "overall_risk": {
                        "label": "caution limited",
                        "p_top": 0.6,
                    }
                },
                "reason_category": "model_score_disagreement",
                "explanation": "The analyst proposes a tier shift based on the current evidence.",
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        assert created["status"] == "open", created

        invalid_risk_response = client.post(
            "/api/disputes",
            json={
                "server_id": "server-test-1",
                "submitted_by": "analyst@example.com",
                "proposed_overall_risk": "NOT_A_TIER",
                "proposed_axes": {},
                "reason_category": "model_score_disagreement",
                "explanation": "This invalid risk tier must be rejected.",
            },
        )
        assert invalid_risk_response.status_code == 422, invalid_risk_response.text

        unknown_server_response = client.post(
            "/api/disputes",
            json={
                "server_id": "missing-server",
                "submitted_by": "analyst@example.com",
                "proposed_overall_risk": "CAUTION_LIMITED",
                "proposed_axes": {},
                "reason_category": "model_score_disagreement",
                "explanation": "The unknown server must be rejected.",
            },
        )
        assert unknown_server_response.status_code == 404, unknown_server_response.text

        read_response = client.get(f"/api/disputes/{created['id']}")
        assert read_response.status_code == 200, read_response.text
        assert read_response.json()["server_id"] == "server-test-1", read_response.text

    print("PASS")