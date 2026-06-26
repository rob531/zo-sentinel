from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import os

router = APIRouter()

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/zo_sentinel")
engine = create_engine(DATABASE_URL)

class SignalScore(BaseModel):
    signal_name: str
    score: float
    confidence: float
    evidence: str

class MCPVerdictDetail(BaseModel):
    mcp_id: str
    risk_tier: str
    composite_score: float
    signal_scores: List[SignalScore]
    explanation: str

def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()

@router.get("/mcp/{mcp_id}/verdict_detail", response_model=MCPVerdictDetail)
async def get_verdict_detail(mcp_id: str, db: Session = Depends(get_db)):
    # Query composite score and risk tier
    composite_query = text("""
        SELECT
            mcp_id,
            composite_score,
            risk_tier,
            explanation
        FROM mcp_composite_scores
        WHERE mcp_id = :mcp_id
    """)
    composite_result = db.execute(composite_query, {"mcp_id": mcp_id}).fetchone()

    if not composite_result:
        raise HTTPException(status_code=404, detail="MCP not found")

    # Query signal scores
    signal_query = text("""
        SELECT
            signal_name,
            score,
            confidence,
            evidence
        FROM mcp_signal_scores
        WHERE mcp_id = :mcp_id
        UNION ALL
        SELECT
            signal_name,
            score,
            confidence,
            evidence
        FROM mcp_llm_axis_scores
        WHERE mcp_id = :mcp_id
    """)
    signal_results = db.execute(signal_query, {"mcp_id": mcp_id}).fetchall()

    if not signal_results:
        raise HTTPException(status_code=404, detail="Signal scores not found")

    # Prepare response
    verdict_detail = MCPVerdictDetail(
        mcp_id=mcp_id,
        risk_tier=composite_result.risk_tier,
        composite_score=composite_result.composite_score,
        signal_scores=[SignalScore(**dict(row)) for row in signal_results],
        explanation=composite_result.explanation
    )

    return verdict_detail

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test with a valid MCP ID (assuming 1 exists in test data)
    response = client.get("/mcp/1/verdict_detail")
    assert response.status_code == 200
    data = response.json()
    assert "risk_tier" in data
    assert "composite_score" in data
    assert "signal_scores" in data
    assert len(data["signal_scores"]) == 7
    assert all("signal_name" in score for score in data["signal_scores"])
    assert all("score" in score for score in data["signal_scores"])
    assert all("confidence" in score for score in data["signal_scores"])
    assert all("evidence" in score for score in data["signal_scores"])
    assert "explanation" in data

    print("PASS")