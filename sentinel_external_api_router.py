from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScore, MCPScoreDispute
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc

router = APIRouter(prefix="/external")


class AxisScoreInput(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    probs: List[float]
    model_version: str


class ScoringResultsPush(BaseModel):
    server_id: str
    scores: List[AxisScoreInput]
    scored_at: str


class ScoringResultsResponse(BaseModel):
    rows_written: int
    server_id: str


class VerdictAxis(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    scored_at: str


class VerdictResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    axes: List[VerdictAxis]
    criteria_version: str


class ServerSummary(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    verdict: str
    last_assessed: Optional[str] = None


class ServersResponse(BaseModel):
    servers: List[ServerSummary]
    total: int


class DisputeCreate(BaseModel):
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Dict[str, Any]
    reason_category: str
    explanation: str


class DisputeResponse(BaseModel):
    dispute_id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: Dict[str, Any]
    reason_category: str
    explanation: str
    status: str
    created_at: Optional[str] = None


class DisputeListResponse(BaseModel):
    disputes: List[DisputeResponse]


@router.post("/scoring-results-push", response_model=ScoringResultsResponse, status_code=202)
async def push_scoring_results(payload: ScoringResultsPush, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == payload.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    rows = []
    for score in payload.scores:
        rows.append({
            "server_id": payload.server_id,
            "axis_name": score.axis_name,
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "probs": score.probs,
            "model_version": score.model_version,
            "scored_at": payload.scored_at
        })
    resp = requests.post("http://127.0.0.1:8772/write", json={"table": "mcp_llm_axis_scores", "rows": rows, "wait": True})
    resp.raise_for_status()
    return ScoringResultsResponse(rows_written=len(rows), server_id=payload.server_id)


@router.get("/verdict/{server_id}", response_model=VerdictResponse)
async def get_verdict(server_id: str, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    scores = db.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).order_by(desc(MCPLLMAxisScore.scored_at)).all()
    axes = [VerdictAxis(axis_name=s.axis_name, label=s.label, p_top=s.p_top, p_critical=s.p_critical, scored_at=s.scored_at.isoformat() if s.scored_at else "") for s in scores]
    return VerdictResponse(server_id=server.server_id, name=server.name, risk_tier=server.risk_tier, verdict=server.verdict or "", axes=axes, criteria_version=server.criteria_version or "unknown")


@router.get("/servers", response_model=ServersResponse)
async def list_servers(skip: int = 0, limit: int = 100, risk_tier: Optional[str] = None, db: Session = Depends(get_session)):
    limit = min(limit, 100)
    q = db.query(MCPServerRegistry)
    cq = db.query(func.count(MCPServerRegistry.server_id))
    if risk_tier:
        q = q.filter(MCPServerRegistry.risk_tier == risk_tier)
        cq = cq.filter(MCPServerRegistry.risk_tier == risk_tier)
    total = cq.scalar()
    servers = q.offset(skip).limit(limit).all()
    return ServersResponse(servers=[ServerSummary(server_id=s.server_id, name=s.name, risk_tier=s.risk_tier, verdict=s.verdict or "", last_assessed=s.last_assessed.isoformat() if s.last_assessed else None) for s in servers], total=total)


@router.post("/disputes", response_model=DisputeResponse, status_code=201)
async def create_dispute(dispute: DisputeCreate, db: Session = Depends(get_session)):
    new_dispute = MCPScoreDispute(
        server_id=dispute.server_id,
        submitted_by=dispute.submitted_by,
        proposed_overall_risk=dispute.proposed_overall_risk,
        proposed_axes=dispute.proposed_axes,
        reason_category=dispute.reason_category,
        explanation=dispute.explanation,
        status="pending"
    )
    db.add(new_dispute)
    db.commit()
    db.refresh(new_dispute)
    return DisputeResponse(dispute_id=new_dispute.id, server_id=new_dispute.server_id, submitted_by=new_dispute.submitted_by, proposed_overall_risk=new_dispute.proposed_overall_risk, proposed_axes=new_dispute.proposed_axes, reason_category=new_dispute.reason_category, explanation=new_dispute.explanation, status=new_dispute.status, created_at=new_dispute.created_at.isoformat() if new_dispute.created_at else None)


@router.get("/disputes/{server_id}", response_model=DisputeListResponse)
async def get_disputes(server_id: str, db: Session = Depends(get_session)):
    disputes = db.query(MCPScoreDispute).filter(MCPScoreDispute.server_id == server_id).order_by(desc(MCPScoreDispute.created_at)).all()
    return DisputeListResponse(disputes=[DisputeResponse(dispute_id=d.id, server_id=d.server_id, submitted_by=d.submitted_by, proposed_overall_risk=d.proposed_overall_risk, proposed_axes=d.proposed_axes, reason_category=d.reason_category, explanation=d.explanation, status=d.status, created_at=d.created_at.isoformat() if d.created_at else None) for d in disputes])


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from unittest.mock import patch, MagicMock
    import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(router)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rows_written": 2}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        sr = client.post("/external/scoring-results-push", json={"server_id": "srv-001", "scores": [{"axis_name": "security", "label": "medium", "p_top": 0.3, "p_critical": 0.1, "probs": [0.3, 0.5, 0.2], "model_version": "v1"}], "scored_at": "2024-01-01T00:00:00"})
        assert sr.status_code == 202
        assert sr.json()["rows_written"] == 2
        print("PASS: POST /external/scoring-results-push")

    srv = MCPServerRegistry(server_id="srv-001", name="Test Server", risk_tier="low", verdict="clean", criteria_version="v1")
    test_session.add(srv)
    sc = MCPLLMAxisScore(server_id="srv-001", axis_name="security", label="low", p_top=0.8, p_critical=0.1, probs=[0.8, 0.15, 0.05], model_version="v1", scored_at=datetime(2024, 1, 1))
    test_session.add(sc)
    test_session.commit()

    vr = client.get("/external/verdict/srv-001")
    assert vr.status_code == 200
    data = vr.json()
    assert data["server_id"] == "srv-001"
    assert data["risk_tier"] == "low"
    assert len(data["axes"]) == 1
    print("PASS: GET /external/verdict/{server_id}")

    svr = client.get("/external/servers?skip=0&limit=10")
    assert svr.status_code == 200
    data = svr.json()
    assert data["total"] == 1
    assert len(data["servers"]) == 1
    print("PASS: GET /external/servers")

    dp = client.post("/external/disputes", json={"server_id": "srv-001", "submitted_by": "user@example.com", "proposed_overall_risk": "medium", "proposed_axes": {"security": "high"}, "reason_category": "misclassification", "explanation": "Server shows signs of activity"})
    assert dp.status_code == 201
    data = dp.json()
    assert "dispute_id" in data
    print("PASS: POST /external/disputes")

    dpr = client.get("/external/disputes/srv-001")
    assert dpr.status_code == 200
    data = dpr.json()
    assert len(data["disputes"]) == 1
    print("PASS: GET /external/disputes/{server_id}")