from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from typing import List, Dict, Any
from app.db import get_session
from app.models import GateSchedulerJobRun
from fastapi.testclient import TestClient
import app.db

router = APIRouter()

class GateAttributionReport:
    directive: str
    success_count: int
    failure_count: int
    average_runtime: float
    last_run_time: datetime

@router.get("/gate/attribution", response_model=List[GateAttributionReport])
async def get_gate_attribution_report(db: Session = Depends(get_session)) -> List[GateAttributionReport]:
    results = db.query(
        GateSchedulerJobRun.directive,
        func.count(GateSchedulerJobRun.id).label("total_runs"),
        func.sum(func.case(
            (GateSchedulerJobRun.status == "success", 1),
            else_=0
        )).label("success_count"),
        func.sum(func.case(
            (GateSchedulerJobRun.status == "failure", 1),
            else_=0
        )).label("failure_count"),
        func.avg(GateSchedulerJobRun.runtime).label("average_runtime"),
        func.max(GateSchedulerJobRun.start_time).label("last_run_time")
    ).group_by(GateSchedulerJobRun.directive).all()

    reports = []
    for result in results:
        reports.append({
            "directive": result.directive,
            "success_count": result.success_count,
            "failure_count": result.failure_count,
            "average_runtime": round(result.average_runtime, 2) if result.average_runtime else 0,
            "last_run_time": result.last_run_time.isoformat() if result.last_run_time else None
        })

    return reports

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Insert test data
    test_db = SessionLocal()
    test_db.add_all([
        GateSchedulerJobRun(
            directive="test_directive_1",
            status="success",
            runtime=10.5,
            start_time=datetime.now()
        ),
        GateSchedulerJobRun(
            directive="test_directive_1",
            status="failure",
            runtime=5.2,
            start_time=datetime.now()
        ),
        GateSchedulerJobRun(
            directive="test_directive_2",
            status="success",
            runtime=8.3,
            start_time=datetime.now()
        )
    ])
    test_db.commit()

    client = TestClient(app)
    response = client.get("/gate/attribution")
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert all(field in response.json()[0] for field in ["directive", "success_count", "failure_count", "average_runtime", "last_run_time"])
    print("PASS")