from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import requests
from app.db import get_session
from app.models import cadence_job_runs

router = APIRouter()

class WaveLedgerRow(BaseModel):
    wave_id: str
    job: str
    started_at: datetime
    finished_at: datetime
    rows_affected: int
    wall_seconds: float
    servers_per_hour: float
    cost_index: float

class WaveLedgerSummary(BaseModel):
    total_waves: int
    total_servers_scored: int
    total_wall_seconds: float
    avg_cost_per_server: float
    peak_throughput: float
    cost_trend_pct: float

class ErrorResponse(BaseModel):
    error: str
    detail: str

def get_wave_ledger_data(hours: int = 720) -> List[WaveLedgerRow]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    query = f"""
    SELECT
        wave_id,
        job,
        started_at,
        finished_at,
        rows_affected,
        EXTRACT(EPOCH FROM (finished_at - started_at)) as wall_seconds
    FROM cadence_job_runs
    WHERE
        job IN ('signal_analyser', 'trust_synthesiser', 'scoring_consumer')
        AND status = 'completed'
        AND started_at >= '{cutoff.isoformat()}'
    ORDER BY started_at
    """
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    data = response.json()
    ledger = []
    for row in data:
        wall_seconds = row['wall_seconds']
        servers_per_hour = (row['rows_affected'] / wall_seconds) * 3600 if wall_seconds > 0 else 0
        cost_index = servers_per_hour * wall_seconds / 3600 if wall_seconds > 0 else 0
        ledger.append(WaveLedgerRow(
            wave_id=row['wave_id'],
            job=row['job'],
            started_at=row['started_at'],
            finished_at=row['finished_at'],
            rows_affected=row['rows_affected'],
            wall_seconds=wall_seconds,
            servers_per_hour=servers_per_hour,
            cost_index=cost_index
        ))
    return ledger

@router.get("/scoring/wave-cost-ledger", response_model=List[WaveLedgerRow])
async def get_wave_cost_ledger(hours: int = Query(720, description="Hours of history to query")):
    try:
        return get_wave_ledger_data(hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scoring/wave-cost-ledger/summary", response_model=WaveLedgerSummary)
async def get_wave_cost_ledger_summary():
    try:
        ledger = get_wave_ledger_data()
        if not ledger:
            return WaveLedgerSummary(
                total_waves=0,
                total_servers_scored=0,
                total_wall_seconds=0,
                avg_cost_per_server=0,
                peak_throughput=0,
                cost_trend_pct=0
            )

        total_waves = len(ledger)
        total_servers_scored = sum(row.rows_affected for row in ledger)
        total_wall_seconds = sum(row.wall_seconds for row in ledger)
        avg_cost_per_server = sum(row.cost_index for row in ledger) / total_waves if total_waves > 0 else 0
        peak_throughput = max(row.servers_per_hour for row in ledger) if total_waves > 0 else 0

        # Simple cost trend: compare first half to second half
        half = total_waves // 2
        first_half_cost = sum(row.cost_index for row in ledger[:half]) / half if half > 0 else 0
        second_half_cost = sum(row.cost_index for row in ledger[half:]) / (total_waves - half) if (total_waves - half) > 0 else 0
        cost_trend_pct = ((second_half_cost - first_half_cost) / first_half_cost * 100) if first_half_cost > 0 else 0

        return WaveLedgerSummary(
            total_waves=total_waves,
            total_servers_scored=total_servers_scored,
            total_wall_seconds=total_wall_seconds,
            avg_cost_per_server=avg_cost_per_server,
            peak_throughput=peak_throughput,
            cost_trend_pct=cost_trend_pct
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_data = [
        {
            "wave_id": "wave1",
            "job": "signal_analyser",
            "status": "completed",
            "started_at": datetime.utcnow() - timedelta(hours=2),
            "finished_at": datetime.utcnow() - timedelta(hours=1),
            "rows_affected": 1000
        },
        {
            "wave_id": "wave2",
            "job": "trust_synthesiser",
            "status": "completed",
            "started_at": datetime.utcnow() - timedelta(hours=1),
            "finished_at": datetime.utcnow(),
            "rows_affected": 2000
        }
    ]
    for data in test_data:
        test_session.add(cadence_job_runs(**data))
    test_session.commit()

    # Run tests
    client = TestClient(app)

    # Test wave ledger endpoint
    response = client.get("/scoring/wave-cost-ledger")
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Test summary endpoint
    response = client.get("/scoring/wave-cost-ledger/summary")
    assert response.status_code == 200
    assert response.json()["total_waves"] == 2
    assert response.json()["total_servers_scored"] == 3000

    print("PASS")