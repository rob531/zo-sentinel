from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter()


class RunResponse(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    rows_affected: int
    detail: Optional[str]


class JobRunsResponse(BaseModel):
    job: str
    runs: List[RunResponse]


@router.get("/api/cadence/jobs/{job}/runs", response_model=JobRunsResponse)
def get_cadence_job_runs(
    job: str,
    limit: int = Query(default=20, ge=1),
    db: Session = Depends(get_session),
):
    try:
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
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            yield TestingSessionLocal()
        finally:
            pass

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    db = TestingSessionLocal()
    now = datetime.utcnow()
    earlier = datetime(2024, 1, 15, 10, 0, 0)

    db.add(CadenceJobRun(job="scan_servers", status="completed", started_at=now, finished_at=now, rows_affected=100, detail="Scan OK"))
    db.add(CadenceJobRun(job="scan_servers", status="failed", started_at=earlier, finished_at=earlier, rows_affected=0, detail="Scan failed"))
    db.add(CadenceJobRun(job="scan_servers", status="completed", started_at=earlier, finished_at=earlier, rows_affected=50, detail="Scan done"))
    db.commit()
    db.close()

    response = client.get("/api/cadence/jobs/scan_servers/runs?limit=3")
    data = response.json()

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert len(data["runs"]) == 3, f"Expected 3 runs, got {len(data['runs'])}"
    assert data["runs"][0]["status"] == "completed", f"Expected completed, got {data['runs'][0]['status']}"

    print("PASS")