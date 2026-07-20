from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.db import get_session
from app.models import MCPLlmAxisScore

router = APIRouter()

class AxisDelta(BaseModel):
    axis_name: str
    delta: float

class DeltaSnapshot(BaseModel):
    scored_at: datetime
    overall_risk_before: float
    overall_risk_after: float
    delta: float
    axis_deltas: List[AxisDelta]

def compute_deltas(scores: List[MCPLlmAxisScore]) -> List[DeltaSnapshot]:
    if len(scores) < 2:
        return []

    deltas = []
    lookback_window = timedelta(days=30)

    for i in range(1, len(scores)):
        prev = scores[i-1]
        curr = scores[i]

        if curr.scored_at - prev.scored_at > lookback_window:
            continue

        overall_delta = curr.overall_risk - prev.overall_risk
        axis_deltas = []

        for axis in curr.axes:
            prev_axis = next((a for a in prev.axes if a.axis_name == axis.axis_name), None)
            if prev_axis and axis.label_index != prev_axis.label_index:
                axis_deltas.append(AxisDelta(axis_name=axis.axis_name, delta=axis.score - prev_axis.score))

        deltas.append(DeltaSnapshot(
            scored_at=curr.scored_at,
            overall_risk_before=prev.overall_risk,
            overall_risk_after=curr.overall_risk,
            delta=overall_delta,
            axis_deltas=axis_deltas
        ))

    return deltas

@router.get("/servers/{server_id}/score-delta-timeline", response_model=List[DeltaSnapshot])
async def get_score_delta_timeline(server_id: int, session: Session = Depends(get_session)):
    scores = session.query(MCPLlmAxisScore).filter(MCPLlmAxisScore.server_id == server_id).order_by(MCPLlmAxisScore.scored_at).all()
    if not scores:
        raise HTTPException(status_code=404, detail="No scores found for server")

    return compute_deltas(scores)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine, get_session
    from app.models import MCPLlmAxisScore, MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    test_engine = engine
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    with TestSession() as session:
        server = MCPServerRegistry(id=1, name="Test Server")
        session.add(server)

        # Add some test scores
        now = datetime.now()
        scores = [
            MCPLlmAxisScore(
                server_id=1,
                scored_at=now - timedelta(days=3),
                overall_risk=0.5,
                axes=[
                    {"axis_name": "axis1", "score": 0.3, "label_index": 1},
                    {"axis_name": "axis2", "score": 0.2, "label_index": 0}
                ]
            ),
            MCPLlmAxisScore(
                server_id=1,
                scored_at=now - timedelta(days=1),
                overall_risk=0.7,
                axes=[
                    {"axis_name": "axis1", "score": 0.4, "label_index": 2},
                    {"axis_name": "axis2", "score": 0.2, "label_index": 0}
                ]
            )
        ]
        session.add_all(scores)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/1/score-delta-timeline")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert len(data[0]["axis_deltas"]) > 0

    print("PASS")