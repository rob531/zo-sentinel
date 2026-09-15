# deps: fastapi, pydantic, sqlalchemy
"""cadence_job_run_detail — query cadence job run history for a given job.

GET /api/cadence/jobs/{job}/runs   List runs for a job (most-recent first).

Auth: public.
Data: app tier via get_session + CadenceJobRun.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence_job_run_detail"])


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: int
    detail: Optional[str] = None


class JobRunsResponse(BaseModel):
    job: str
    runs: list[RunResponse]


@router.get("/{job}/runs", response_model=JobRunsResponse)
def get_cadence_job_runs(
    job: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> JobRunsResponse:
    runs = (
        db.query(CadenceJobRun)
        .filter(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return JobRunsResponse(
        job=job,
        runs=[
            RunResponse(
                id=r.id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                rows_affected=r.rows_affected,
                detail=r.detail,
            )
            for r in runs
        ],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base
    from app.main import app as main_app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)

    db = TestingSession()
    now = datetime.utcnow()
    earlier = datetime(2024, 1, 15, 10, 0, 0)

    db.add(CadenceJobRun(job="scan_servers", status="completed", started_at=now, finished_at=now, rows_affected=100, detail="Scan OK"))
    db.add(CadenceJobRun(job="scan_servers", status="failed", started_at=earlier, finished_at=earlier, rows_affected=0, detail="Scan failed"))
    db.add(CadenceJobRun(job="scan_servers", status="completed", started_at=earlier, finished_at=earlier, rows_affected=50, detail="Scan done"))
    db.commit()
    db.close()

    response = client.get("/api/cadence/jobs/scan_servers/runs?limit=3")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["runs"]) == 3, f"Expected 3 runs, got {len(data['runs'])}"
    assert data["runs"][0]["status"] == "completed", f"Expected completed, got {data['runs'][0]['status']}"

    print("PASS")
