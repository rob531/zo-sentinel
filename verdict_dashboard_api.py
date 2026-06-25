from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from ..database import get_db
from ..models import McpLlmAxisScores, McpLlmVerdicts, McpLlmSignals

router = APIRouter(
    prefix="/api/verdict-dashboard",
    tags=["verdict-dashboard"],
    responses={404: {"description": "Not found"}},
)

class SignalScore(BaseModel):
    signal_id: int
    signal_name: str
    score: float
    weight: float
    description: Optional[str] = None

class VerdictData(BaseModel):
    verdict_id: int
    mcp_id: int
    overall_score: float
    risk_tier: str
    created_at: datetime
    signals: List[SignalScore]

@router.get("/verdicts/{verdict_id}", response_model=VerdictData)
def get_verdict_details(verdict_id: int, db: Session = Depends(get_db)):
    # Get verdict details
    verdict = db.query(McpLlmVerdicts).filter(McpLlmVerdicts.id == verdict_id).first()
    if not verdict:
        raise HTTPException(status_code=404, detail="Verdict not found")

    # Get axis scores for this verdict
    axis_scores = db.query(McpLlmAxisScores).filter(
        McpLlmAxisScores.verdict_id == verdict_id
    ).all()

    # Get signals for each axis
    signals = []
    for axis in axis_scores:
        axis_signals = db.query(McpLlmSignals).filter(
            McpLlmSignals.axis_id == axis.id
        ).all()

        for signal in axis_signals:
            signals.append({
                "signal_id": signal.id,
                "signal_name": signal.name,
                "score": signal.score,
                "weight": signal.weight,
                "description": signal.description
            })

    return {
        "verdict_id": verdict.id,
        "mcp_id": verdict.mcp_id,
        "overall_score": verdict.overall_score,
        "risk_tier": verdict.risk_tier,
        "created_at": verdict.created_at,
        "signals": signals
    }

@router.get("/verdicts/", response_model=List[VerdictData])
def get_all_verdicts(
    mcp_id: Optional[int] = None,
    risk_tier: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(McpLlmVerdicts)

    if mcp_id:
        query = query.filter(McpLlmVerdicts.mcp_id == mcp_id)
    if risk_tier:
        query = query.filter(McpLlmVerdicts.risk_tier == risk_tier)

    verdicts = query.limit(limit).all()

    result = []
    for verdict in verdicts:
        # Get axis scores for this verdict
        axis_scores = db.query(McpLlmAxisScores).filter(
            McpLlmAxisScores.verdict_id == verdict.id
        ).all()

        # Get signals for each axis
        signals = []
        for axis in axis_scores:
            axis_signals = db.query(McpLlmSignals).filter(
                McpLlmSignals.axis_id == axis.id
            ).all()

            for signal in axis_signals:
                signals.append({
                    "signal_id": signal.id,
                    "signal_name": signal.name,
                    "score": signal.score,
                    "weight": signal.weight,
                    "description": signal.description
                })

        result.append({
            "verdict_id": verdict.id,
            "mcp_id": verdict.mcp_id,
            "overall_score": verdict.overall_score,
            "risk_tier": verdict.risk_tier,
            "created_at": verdict.created_at,
            "signals": signals
        })

    return result

# Test cases
def test_verdict_dashboard():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # Test get single verdict
    response = client.get("/api/verdict-dashboard/verdicts/1")
    assert response.status_code == 200
    assert "verdict_id" in response.json()
    assert "signals" in response.json()

    # Test get all verdicts
    response = client.get("/api/verdict-dashboard/verdicts/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test filter by mcp_id
    response = client.get("/api/verdict-dashboard/verdicts/?mcp_id=1")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test filter by risk_tier
    response = client.get("/api/verdict-dashboard/verdicts/?risk_tier=high")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    print("All tests passed!")