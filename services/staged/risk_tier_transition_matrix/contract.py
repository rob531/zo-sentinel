from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
import requests
from sqlalchemy import func

router = APIRouter(prefix="/api/risk")

class RiskTierTransition(BaseModel):
    from_tier: str
    to_tier: str
    count: int

class TransitionMatrixResponse(BaseModel):
    matrix: List[RiskTierTransition]
    total_transitions: int
    period_days: int

def get_risk_tier(overall_risk: float) -> str:
    if overall_risk > 75:
        return "TRUSTED_GENERAL"
    elif overall_risk > 60:
        return "TRUSTED_RESEARCH"
    elif overall_risk > 45:
        return "ENTERPRISE_CONTROLLED"
    elif overall_risk > 30:
        return "CAUTION_LIMITED"
    elif overall_risk > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def get_transition_matrix(db: Session, period_days: int = 30) -> TransitionMatrixResponse:
    cutoff_date = datetime.utcnow() - timedelta(days=period_days)

    # Get all servers with scores in the period
    servers = db.query(McpServerRegistry.id).join(
        McpLlmAxisScore,
        McpServerRegistry.id == McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.axis == 'overall_risk',
        McpLlmAxisScore.scored_at >= cutoff_date
    ).group_by(
        McpServerRegistry.id
    ).all()

    transitions = []
    for server in servers:
        server_id = server.id
        # Get all scores for this server in the period, ordered by time
        scores = db.query(
            McpLlmAxisScore.score,
            McpLlmAxisScore.scored_at
        ).filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis == 'overall_risk',
            McpLlmAxisScore.scored_at >= cutoff_date
        ).order_by(
            McpLlmAxisScore.scored_at
        ).all()

        # Find transitions
        for i in range(1, len(scores)):
            prev_score = scores[i-1].score
            curr_score = scores[i].score
            prev_tier = get_risk_tier(prev_score)
            curr_tier = get_risk_tier(curr_score)

            if prev_tier != curr_tier:
                transitions.append({
                    'from_tier': prev_tier,
                    'to_tier': curr_tier,
                    'server_id': server_id
                })

    # Count transitions
    matrix = []
    for transition in transitions:
        exists = False
        for row in matrix:
            if row['from_tier'] == transition['from_tier'] and row['to_tier'] == transition['to_tier']:
                row['count'] += 1
                exists = True
                break
        if not exists:
            matrix.append({
                'from_tier': transition['from_tier'],
                'to_tier': transition['to_tier'],
                'count': 1
            })

    return TransitionMatrixResponse(
        matrix=matrix,
        total_transitions=len(transitions),
        period_days=period_days
    )

@router.get("/transition-matrix", response_model=TransitionMatrixResponse)
async def risk_tier_transition_matrix(
    period_days: Optional[int] = 30,
    db: Session = Depends(get_session)
):
    try:
        return get_transition_matrix(db, period_days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Seed test data
    with TestSession() as session:
        # Create 5 servers
        servers = [
            McpServerRegistry(id=f"server{i}", name=f"Server {i}")
            for i in range(1, 6)
        ]
        session.add_all(servers)
        session.commit()

        # Add scores that will create transitions
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)

        # Server 1: TRUSTED_GENERAL -> TRUSTED_RESEARCH
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis="overall_risk",
            score=80,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis="overall_risk",
            score=65,
            scored_at=now
        ))

        # Server 2: TRUSTED_RESEARCH -> ENTERPRISE_CONTROLLED
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis="overall_risk",
            score=65,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis="overall_risk",
            score=50,
            scored_at=now
        ))

        # Server 3: ENTERPRISE_CONTROLLED -> CAUTION_LIMITED
        session.add(McpLlmAxisScore(
            server_id="server3",
            axis="overall_risk",
            score=50,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server3",
            axis="overall_risk",
            score=35,
            scored_at=now
        ))

        # Server 4: CAUTION_LIMITED -> HIGH_RISK_ISOLATED
        session.add(McpLlmAxisScore(
            server_id="server4",
            axis="overall_risk",
            score=35,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server4",
            axis="overall_risk",
            score=20,
            scored_at=now
        ))

        # Server 5: HIGH_RISK_ISOLATED -> KNOWN_THREAT
        session.add(McpLlmAxisScore(
            server_id="server5",
            axis="overall_risk",
            score=20,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server5",
            axis="overall_risk",
            score=10,
            scored_at=now
        ))

        session.commit()

    # Test the endpoint
    response = client.get("/api/risk/transition-matrix")
    assert response.status_code == 200
    data = response.json()

    assert len(data["matrix"]) >= 3
    assert data["total_transitions"] >= 3

    print("PASS")