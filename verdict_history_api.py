from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPLLMAxisScore, MCPServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import desc

router = APIRouter()

class AxisSummary(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

class VerdictRecord(BaseModel):
    scored_at: str
    model_version: str
    axes: List[AxisSummary]
    overall_risk: float
    risk_tier: str
    decision_rule_version: str

class VerdictHistoryResponse(BaseModel):
    server_id: str
    records: List[VerdictRecord]

def get_verdict_history(server_id: str, limit: int = 30, offset: int = 0, db: Session = Depends(get_session)) -> VerdictHistoryResponse:
    # Check if server exists
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Query axis scores
    scores = db.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id
    ).order_by(
        desc(MCPLLMAxisScore.scored_at)
    ).limit(limit).offset(offset).all()

    records = []
    for score in scores:
        axes = []
        for axis in score.axes:
            axes.append(AxisSummary(
                axis_name=axis.axis_name,
                label=axis.label,
                p_top=axis.p_top,
                p_critical=axis.p_critical,
                p_danger=axis.p_danger,
                escalated=axis.escalated
            ))

        records.append(VerdictRecord(
            scored_at=score.scored_at.isoformat(),
            model_version=score.model_version,
            axes=axes,
            overall_risk=score.overall_risk,
            risk_tier=score.risk_tier,
            decision_rule_version=score.decision_rule_version
        ))

    return VerdictHistoryResponse(server_id=server_id, records=records)

@router.get("/servers/{server_id}/verdict-history", response_model=VerdictHistoryResponse)
async def verdict_history(
    server_id: str,
    limit: int = Query(30, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_session)
):
    return get_verdict_history(server_id, limit, offset, db)

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test data
    db = SessionLocal()
    test_server = MCPServerRegistry(server_id="test-server-001")
    db.add(test_server)
    db.commit()

    test_score = MCPLLMAxisScore(
        server_id="test-server-001",
        scored_at=datetime.now(),
        model_version="1.0",
        axes=[{
            "axis_name": "test_axis",
            "label": "test_label",
            "p_top": 0.9,
            "p_critical": 0.8,
            "p_danger": 0.7,
            "escalated": False
        }],
        overall_risk=0.85,
        risk_tier="high",
        decision_rule_version="1.0"
    )
    db.add(test_score)
    db.commit()

    client = TestClient(app)
    resp = client.get('/servers/test-server-001/verdict-history?limit=5')
    assert resp.status_code == 200
    data = resp.json()
    assert 'records' in data
    assert data['server_id'] == 'test-server-001'
    assert isinstance(data['records'], list)
    print('PASS: verdict_history_api smoke')