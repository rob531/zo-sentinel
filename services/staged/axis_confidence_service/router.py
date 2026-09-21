from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import McpLlmAxisScore
import statistics

router = APIRouter(prefix="/api")

class AxisConfidence(BaseModel):
    axis_name: str
    row_count: int
    mean_p_top: float
    std_p_top: float
    min_p_top: float
    max_p_top: float
    p_top_above_70_pct: float
    escalated_pct: float

class AxisConfidenceResponse(BaseModel):
    axes: List[AxisConfidence]

def get_axis_confidence(db: Session = Depends(get_session)) -> AxisConfidenceResponse:
    results = db.query(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.escalated
    ).all()

    axis_data = {}

    for row in results:
        axis_name = row.axis_name
        p_top = row.p_top
        escalated = row.escalated

        if axis_name not in axis_data:
            axis_data[axis_name] = {
                'p_tops': [],
                'escalated_count': 0,
                'total_count': 0
            }

        axis_data[axis_name]['p_tops'].append(p_top)
        axis_data[axis_name]['total_count'] += 1
        if escalated:
            axis_data[axis_name]['escalated_count'] += 1

    axes = []

    for axis_name, data in axis_data.items():
        p_tops = data['p_tops']
        total_count = data['total_count']
        escalated_count = data['escalated_count']

        mean_p_top = statistics.mean(p_tops) if p_tops else 0
        std_p_top = statistics.stdev(p_tops) if len(p_tops) > 1 else 0
        min_p_top = min(p_tops) if p_tops else 0
        max_p_top = max(p_tops) if p_tops else 0
        p_top_above_70_pct = sum(1 for p in p_tops if p > 0.7) / total_count if total_count > 0 else 0
        escalated_pct = escalated_count / total_count if total_count > 0 else 0

        axes.append(AxisConfidence(
            axis_name=axis_name,
            row_count=total_count,
            mean_p_top=mean_p_top,
            std_p_top=std_p_top,
            min_p_top=min_p_top,
            max_p_top=max_p_top,
            p_top_above_70_pct=p_top_above_70_pct,
            escalated_pct=escalated_pct
        ))

    return AxisConfidenceResponse(axes=axes)

@router.get("/axis/confidence", response_model=AxisConfidenceResponse)
async def axis_confidence_endpoint(db: Session = Depends(get_session)):
    return get_axis_confidence(db)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.test_client import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    async def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Seed test data
    with TestSession() as session:
        test_data = [
            McpLlmAxisScore(axis_name="axis1", p_top=0.8, escalated=True),
            McpLlmAxisScore(axis_name="axis1", p_top=0.6, escalated=False),
            McpLlmAxisScore(axis_name="axis2", p_top=0.9, escalated=True),
            McpLlmAxisScore(axis_name="axis2", p_top=0.75, escalated=False),
            McpLlmAxisScore(axis_name="axis2", p_top=0.85, escalated=True),
        ]
        session.add_all(test_data)
        session.commit()

    response = client.get("/api/axis/confidence")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) >= 1
    for axis in data["axes"]:
        assert isinstance(axis["row_count"], int)
        assert isinstance(axis["mean_p_top"], (int, float))
        assert isinstance(axis["std_p_top"], (int, float))
        assert isinstance(axis["min_p_top"], (int, float))
        assert isinstance(axis["max_p_top"], (int, float))
        assert isinstance(axis["p_top_above_70_pct"], (int, float))
        assert isinstance(axis["escalated_pct"], (int, float))

    print("PASS")