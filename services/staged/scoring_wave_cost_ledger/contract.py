from sqlalchemy.pool import StaticPool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import CadenceJobRun
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import json

router = APIRouter(prefix="/api/scoring")

class WaveCost(BaseModel):
    wave_id: int
    job_name: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    rows_scored: int
    estimated_cost_units: float

class WaveCostsResponse(BaseModel):
    waves: List[WaveCost]

def get_wave_costs(db: Session = Depends(get_session)) -> WaveCostsResponse:
    # Query for scoring wave jobs
    jobs = db.query(CadenceJobRun).filter(
        or_(
            CadenceJobRun.job.like('scoring_wave%'),
            CadenceJobRun.job.like('score_run%')
        )
    ).all()

    waves = []
    for job in jobs:
        try:
            detail = json.loads(job.detail)
            rows_scored = detail.get('rows_scored', 0)
        except (json.JSONDecodeError, AttributeError):
            rows_scored = 0

        duration = (job.finished_at - job.started_at).total_seconds() if job.finished_at else 0
        estimated_cost = rows_scored * duration * 0.001  # Example cost calculation

        waves.append(WaveCost(
            wave_id=job.id,
            job_name=job.job,
            started_at=job.started_at,
            finished_at=job.finished_at,
            duration_seconds=duration,
            rows_scored=rows_scored,
            estimated_cost_units=estimated_cost
        ))

    return WaveCostsResponse(waves=waves)

router.get("/wave-costs")(get_wave_costs)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_session.add_all([
        CadenceJobRun(
            id=1,
            job="scoring_wave_1",
            status="completed",
            started_at=datetime(2023, 1, 1, 10, 0),
            finished_at=datetime(2023, 1, 1, 10, 5),
            rows_affected=1000,
            detail=json.dumps({"rows_scored": 1000})
        ),
        CadenceJobRun(
            id=2,
            job="scoring_wave_2",
            status="completed",
            started_at=datetime(2023, 1, 1, 11, 0),
            finished_at=datetime(2023, 1, 1, 11, 10),
            rows_affected=2000,
            detail=json.dumps({"rows_scored": 2000})
        ),
        CadenceJobRun(
            id=3,
            job="score_run_3",
            status="completed",
            started_at=datetime(2023, 1, 1, 12, 0),
            finished_at=datetime(2023, 1, 1, 12, 15),
            rows_affected=1500,
            detail=json.dumps({})
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/wave-costs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["waves"]) == 3
    assert data["waves"][0]["rows_scored"] > 0
    assert data["waves"][1]["rows_scored"] > 0

    print("PASS")