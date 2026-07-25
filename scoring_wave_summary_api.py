from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import requests
from app.db import get_session
from app.models import cadence_job_runs
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class WaveSummary(BaseModel):
    total_waves: int
    servers_scored: int
    avg_per_wave: float
    last_wave_at: Optional[str]
    error_count: int
    cost_estimate_usd: Optional[float]

def get_write_service_query(query: str) -> dict:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query},
        timeout=10
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Write service query failed")
    return response.json()

@router.get("/scoring/waves/summary", response_model=WaveSummary)
async def scoring_wave_summary(db: Session = Depends(get_session)):
    # Query for scoring-wave jobs
    query = """
    SELECT
        COUNT(*) as total_waves,
        SUM(rows_affected) as total_servers_scored,
        AVG(rows_affected) as avg_servers_per_wave,
        MAX(finished_at) as last_wave_at,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as error_count,
        SUM(CAST(detail->>'cost_usd' AS FLOAT)) as cost_estimate_usd
    FROM cadence_job_runs
    WHERE job LIKE 'score_wave%' OR job = 'scoring_batch'
    """

    # Execute query via write service
    result = get_write_service_query(query)
    data = result.get("data", {})

    # Process results
    total_waves = data.get("total_waves", 0)
    servers_scored = data.get("total_servers_scored", 0)
    avg_per_wave = data.get("avg_servers_per_wave", 0.0)
    last_wave_at = data.get("last_wave_at")
    error_count = data.get("error_count", 0)
    cost_estimate_usd = data.get("cost_estimate_usd")

    return WaveSummary(
        total_waves=total_waves,
        servers_scored=servers_scored,
        avg_per_wave=avg_per_wave,
        last_wave_at=last_wave_at,
        error_count=error_count,
        cost_estimate_usd=cost_estimate_usd
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.models import cadence_job_runs
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    test_session = sessionmaker(bind=test_engine)
    test_db = test_session()

    # Create test table
    cadence_job_runs.__table__.create(test_engine)

    # Add test data
    test_data = [
        cadence_job_runs(
            id=1,
            job="score_wave_1",
            status="completed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            rows_affected=100,
            detail={"cost_usd": 1.50}
        ),
        cadence_job_runs(
            id=2,
            job="score_wave_2",
            status="failed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            rows_affected=50,
            detail={"cost_usd": 0.75}
        ),
        cadence_job_runs(
            id=3,
            job="scoring_batch",
            status="completed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            rows_affected=200,
            detail={"cost_usd": 3.00}
        ),
        cadence_job_runs(
            id=4,
            job="score_wave_3",
            status="completed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            rows_affected=75,
            detail={"cost_usd": 1.12}
        ),
        cadence_job_runs(
            id=5,
            job="score_wave_4",
            status="failed",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            rows_affected=0,
            detail={"cost_usd": 0.00}
        )
    ]
    test_db.add_all(test_data)
    test_db.commit()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_db

    # Create test client
    client = TestClient(router)

    # Test endpoint
    response = client.get("/scoring/waves/summary")
    assert response.status_code == 200
    assert response.json() == {
        "total_waves": 5,
        "servers_scored": 425,
        "avg_per_wave": 106.25,
        "last_wave_at": str(datetime.now()),
        "error_count": 2,
        "cost_estimate_usd": 6.37
    }

    print("PASS")