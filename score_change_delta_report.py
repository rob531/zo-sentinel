from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from datetime import datetime, timedelta

router = APIRouter()

class AxisDelta(BaseModel):
    axis_name: str
    prev_p_top: float
    curr_p_top: float
    delta: float

class TierChange(BaseModel):
    prev_tier: str
    curr_tier: str

class ServerDelta(BaseModel):
    server_id: str
    axis_deltas: List[AxisDelta]
    tier_change: TierChange
    scored_at: datetime

class ScoreDeltaReportResponse(BaseModel):
    results: List[ServerDelta]

def get_recent_waves(session: Session, since_minutes: int = 1440) -> List[datetime]:
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
    waves = session.query(MCPLLMAxisScores.scored_at).distinct().filter(
        MCPLLMAxisScores.scored_at >= cutoff
    ).order_by(MCPLLMAxisScores.scored_at.desc()).limit(2).all()
    return [wave[0] for wave in waves]

def get_server_deltas(session: Session, prev_wave: datetime, curr_wave: datetime, risk_tier_filter: Optional[str] = None) -> List[ServerDelta]:
    query = session.query(
        MCPServerRegistry.id,
        MCPServerRegistry.risk_tier,
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.scored_at
    ).join(
        MCPLLMAxisScores, MCPServerRegistry.id == MCPLLMAxisScores.server_id
    ).filter(
        MCPLLMAxisScores.scored_at.in_([prev_wave, curr_wave])
    )

    if risk_tier_filter:
        query = query.filter(MCPServerRegistry.risk_tier == risk_tier_filter)

    results = query.all()

    server_data = {}
    for row in results:
        server_id, risk_tier, axis_name, p_top, scored_at = row
        if server_id not in server_data:
            server_data[server_id] = {
                'prev': {'tier': None, 'axes': {}},
                'curr': {'tier': None, 'axes': {}},
                'scored_at': scored_at
            }

        if scored_at == prev_wave:
            server_data[server_id]['prev']['tier'] = risk_tier
            server_data[server_id]['prev']['axes'][axis_name] = p_top
        else:
            server_data[server_id]['curr']['tier'] = risk_tier
            server_data[server_id]['curr']['axes'][axis_name] = p_top

    deltas = []
    for server_id, data in server_data.items():
        prev_data = data['prev']
        curr_data = data['curr']

        axis_deltas = []
        for axis_name in prev_data['axes']:
            prev_p_top = prev_data['axes'][axis_name]
            curr_p_top = curr_data['axes'].get(axis_name, 0)
            delta = curr_p_top - prev_p_top
            axis_deltas.append(AxisDelta(
                axis_name=axis_name,
                prev_p_top=prev_p_top,
                curr_p_top=curr_p_top,
                delta=delta
            ))

        deltas.append(ServerDelta(
            server_id=server_id,
            axis_deltas=axis_deltas,
            tier_change=TierChange(
                prev_tier=prev_data['tier'],
                curr_tier=curr_data['tier']
            ),
            scored_at=data['scored_at']
        ))

    return deltas

@router.get("/delta-report", response_model=ScoreDeltaReportResponse)
async def get_delta_report(
    session: Session = Depends(get_session),
    since_minutes: int = Query(1440, description="Minutes since last scoring wave"),
    risk_tier_filter: Optional[str] = Query(None, description="Filter by risk tier")
):
    waves = get_recent_waves(session, since_minutes)
    if len(waves) < 2:
        return ScoreDeltaReportResponse(results=[])

    prev_wave, curr_wave = waves
    deltas = get_server_deltas(session, prev_wave, curr_wave, risk_tier_filter)
    return ScoreDeltaReportResponse(results=deltas)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry, MCPLLMAxisScores
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_server = MCPServerRegistry(
        id="test-server-1",
        risk_tier="high"
    )
    test_session.add(test_server)

    prev_wave = datetime.utcnow() - timedelta(hours=2)
    curr_wave = datetime.utcnow()

    test_scores = [
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="axis1",
            p_top=0.8,
            scored_at=prev_wave
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="axis2",
            p_top=0.6,
            scored_at=prev_wave
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="axis1",
            p_top=0.9,
            scored_at=curr_wave
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="axis2",
            p_top=0.5,
            scored_at=curr_wave
        )
    ]
    test_session.add_all(test_scores)
    test_session.commit()

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/scoring/delta-report")
    assert response.status_code == 200
    data = response.json()

    assert len(data["results"]) == 1
    server_delta = data["results"][0]
    assert server_delta["server_id"] == "test-server-1"
    assert len(server_delta["axis_deltas"]) == 2

    axis1_delta = next(d for d in server_delta["axis_deltas"] if d["axis_name"] == "axis1")
    assert axis1_delta["prev_p_top"] == 0.8
    assert axis1_delta["curr_p_top"] == 0.9
    assert axis1_delta["delta"] == 0.1

    axis2_delta = next(d for d in server_delta["axis_deltas"] if d["axis_name"] == "axis2")
    assert axis2_delta["prev_p_top"] == 0.6
    assert axis2_delta["curr_p_top"] == 0.5
    assert axis2_delta["delta"] == -0.1

    assert server_delta["tier_change"]["prev_tier"] == "high"
    assert server_delta["tier_change"]["curr_tier"] == "high"

    print("PASS")