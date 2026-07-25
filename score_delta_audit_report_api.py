from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPLLMAxisScore, MCPServerRegistry
from sqlalchemy import func, and_, or_
import requests

router = APIRouter()

class DeltaReportRequest(BaseModel):
    lookback_hours: Optional[int] = 720
    min_delta_threshold: Optional[float] = 5.0

class AxisDeltas(BaseModel):
    overall_risk_delta: float
    axis_deltas: Dict[str, float]

class ServerDelta(BaseModel):
    server_id: str
    name: str
    change: AxisDeltas

class DeltaReportResponse(BaseModel):
    generated_at: str
    lookback_hours: int
    summary: Dict[str, int]
    servers: List[ServerDelta]

def get_recent_scores(db, lookback_hours: int):
    cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    subq = (
        db.query(
            MCPLLMAxisScore.server_id,
            MCPLLMAxisScore.axis_name,
            MCPLLMAxisScore.scored_at,
            func.row_number().over(
                partition_by=[MCPLLMAxisScore.server_id, MCPLLMAxisScore.axis_name],
                order_by=MCPLLMAxisScore.scored_at.desc()
            ).label('rn')
        )
        .filter(MCPLLMAxisScore.scored_at >= cutoff)
        .subquery()
    )

    return db.query(MCPLLMAxisScore).join(
        subq,
        and_(
            MCPLLMAxisScore.server_id == subq.c.server_id,
            MCPLLMAxisScore.axis_name == subq.c.axis_name,
            MCPLLMAxisScore.scored_at == subq.c.scored_at,
            subq.c.rn <= 2
        )
    ).all()

def calculate_deltas(scores):
    server_deltas = {}
    axis_names = set()

    # Group scores by server_id and axis_name
    for score in scores:
        key = (score.server_id, score.axis_name)
        if key not in server_deltas:
            server_deltas[key] = []
        server_deltas[key].append(score)
        axis_names.add(score.axis_name)

    # Calculate deltas for each server
    result = []
    for (server_id, axis_name), score_list in server_deltas.items():
        if len(score_list) < 2:
            continue

        # Sort by scored_at (oldest first)
        score_list.sort(key=lambda x: x.scored_at)

        # Calculate delta for this axis
        old_score = score_list[0]
        new_score = score_list[1]
        delta = new_score.p_danger - old_score.p_danger

        # Store delta for this server
        if server_id not in result:
            result.append({
                'server_id': server_id,
                'axis_deltas': {}
            })

        server = next(s for s in result if s['server_id'] == server_id)
        server['axis_deltas'][axis_name] = delta

    # Calculate overall risk delta
    for server in result:
        if not server['axis_deltas']:
            continue

        overall_delta = sum(server['axis_deltas'].values()) / len(server['axis_deltas'])
        server['overall_risk_delta'] = overall_delta

    return result

@router.post("/servers/risk-delta-report", response_model=DeltaReportResponse)
async def get_risk_delta_report(
    request: DeltaReportRequest,
    db=Depends(get_session)
):
    scores = get_recent_scores(db, request.lookback_hours)
    raw_deltas = calculate_deltas(scores)

    # Filter by threshold and get server names
    filtered_deltas = []
    server_ids = set()
    for delta in raw_deltas:
        if abs(delta['overall_risk_delta']) >= request.min_delta_threshold:
            server_ids.add(delta['server_id'])
            filtered_deltas.append(delta)

    servers = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id.in_(server_ids)).all()
    server_map = {s.server_id: s.name for s in servers}

    # Build response
    response_deltas = []
    for delta in filtered_deltas:
        response_deltas.append({
            'server_id': delta['server_id'],
            'name': server_map.get(delta['server_id'], 'Unknown'),
            'change': {
                'overall_risk_delta': delta['overall_risk_delta'],
                'axis_deltas': delta['axis_deltas']
            }
        })

    # Calculate summary
    summary = {
        'total_servers': len(response_deltas),
        'improved': sum(1 for d in response_deltas if d['change']['overall_risk_delta'] < 0),
        'degraded': sum(1 for d in response_deltas if d['change']['overall_risk_delta'] > 0),
        'flat': sum(1 for d in response_deltas if d['change']['overall_risk_delta'] == 0)
    }

    return {
        'generated_at': datetime.utcnow().isoformat(),
        'lookback_hours': request.lookback_hours,
        'summary': summary,
        'servers': response_deltas
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as db:
        # Add test servers
        db.add_all([
            MCPServerRegistry(server_id="s1", name="Server One"),
            MCPServerRegistry(server_id="s2", name="Server Two"),
            MCPServerRegistry(server_id="s3", name="Server Three")
        ])

        # Add test scores
        now = datetime.utcnow()
        db.add_all([
            # Server 1 scores
            MCPLLMAxisScore(server_id="s1", axis_name="auth_strength", p_danger=0.1, scored_at=now - timedelta(hours=1)),
            MCPLLMAxisScore(server_id="s1", axis_name="auth_strength", p_danger=0.2, scored_at=now),
            MCPLLMAxisScore(server_id="s1", axis_name="capability_breadth", p_danger=0.3, scored_at=now - timedelta(hours=1)),
            MCPLLMAxisScore(server_id="s1", axis_name="capability_breadth", p_danger=0.4, scored_at=now),

            # Server 2 scores (no change)
            MCPLLMAxisScore(server_id="s2", axis_name="auth_strength", p_danger=0.5, scored_at=now - timedelta(hours=1)),
            MCPLLMAxisScore(server_id="s2", axis_name="auth_strength", p_danger=0.5, scored_at=now),

            # Server 3 scores (below threshold)
            MCPLLMAxisScore(server_id="s3", axis_name="auth_strength", p_danger=0.6, scored_at=now - timedelta(hours=1)),
            MCPLLMAxisScore(server_id="s3", axis_name="auth_strength", p_danger=0.61, scored_at=now)
        ])
        db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.post("/servers/risk-delta-report", json={"lookback_hours": 24, "min_delta_threshold": 0.1})

    assert response.status_code == 200
    data = response.json()

    assert data['generated_at'] is not None
    assert data['lookback_hours'] == 24
    assert data['summary']['total_servers'] == 2  # s1 (changed) and s2 (flat)
    assert data['summary']['improved'] + data['summary']['degraded'] + data['summary']['flat'] == 2

    # Check server 1 has deltas
    s1 = next(s for s in data['servers'] if s['server_id'] == 's1')
    assert s1['name'] == 'Server One'
    assert s1['change']['overall_risk_delta'] == 0.15  # (0.2+0.4)/2 - (0.1+0.3)/2 = 0.15
    assert 'auth_strength' in s1['change']['axis_deltas']
    assert 'capability_breadth' in s1['change']['axis_deltas']

    # Check server 2 is flat
    s2 = next(s for s in data['servers'] if s['server_id'] == 's2')
    assert s2['name'] == 'Server Two'
    assert s2['change']['overall_risk_delta'] == 0

    print("PASS")