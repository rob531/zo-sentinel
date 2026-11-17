"""Logic and HTTP surface for cadence job run diagnostics."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceRunResponse(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration_seconds: Optional[float]
    rows_affected: Optional[int]
    detail: Optional[str]


class CadenceRunListResponse(BaseModel):
    runs: List[CadenceRunResponse]
    count: int


class CadenceRunDetailResponse(BaseModel):
    run: CadenceRunResponse


def _duration_seconds(
    started_at: Optional[datetime], finished_at: Optional[datetime]
) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def _serialize_run(run: CadenceJobRun) -> dict:
    return {
        "id": run.id,
        "job": run.job,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": _duration_seconds(run.started_at, run.finished_at),
        "rows_affected": run.rows_affected,
        "detail": run.detail,
    }


def list_cadence_runs(
    db: Session,
    job: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[CadenceJobRun]:
    """Return cadence runs matching the filters in newest-first order."""
    limit = max(1, min(int(limit), 200))
    query = select(CadenceJobRun)

    if job is not None:
        query = query.where(CadenceJobRun.job == job)
    if status is not None:
        query = query.where(CadenceJobRun.status == status)

    query = query.order_by(
        case((CadenceJobRun.started_at.is_(None), 1), else_=0).asc(),
        CadenceJobRun.started_at.desc(),
        CadenceJobRun.id.desc(),
    ).limit(limit)

    return list(db.execute(query).scalars().all())


def get_cadence_run(db: Session, run_id: int) -> Optional[CadenceJobRun]:
    """Return one cadence run, or None when it does not exist."""
    return db.get(CadenceJobRun, run_id)


@router.get("/cadence/runs", response_model=CadenceRunListResponse)
def list_runs(
    job: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> CadenceRunListResponse:
    runs = list_cadence_runs(db, job=job, status=status, limit=limit)
    serialized = [_serialize_run(run) for run in runs]
    return CadenceRunListResponse(runs=serialized, count=len(serialized))


@router.get("/cadence/runs/{run_id}", response_model=CadenceRunDetailResponse)
def get_run(run_id: int, db: Session = Depends(get_session)) -> CadenceRunDetailResponse:
    run = get_cadence_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Cadence run not found")
    return CadenceRunDetailResponse(run=_serialize_run(run))


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    base = datetime(2024, 1, 1, 12, 0, 0)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="daily_scan",
                    status="failed",
                    started_at=base,
                    finished_at=base + timedelta(seconds=12),
                    rows_affected=0,
                    detail="failure",
                ),
                CadenceJobRun(
                    job="daily_scan",
                    status="running",
                    started_at=base + timedelta(minutes=1),
                    finished_at=None,
                    rows_affected=None,
                    detail=None,
                ),
                CadenceJobRun(
                    job="hourly_scan",
                    status="ok",
                    started_at=base + timedelta(minutes=2),
                    finished_at=base + timedelta(minutes=2, seconds=3),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    job="hourly_scan",
                    status="ok",
                    started_at=base + timedelta(minutes=3),
                    finished_at=base + timedelta(minutes=3, seconds=4),
                    rows_affected=20,
                    detail=None,
                ),
            ]
        )
        session.commit()

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session
    client = TestClient(test_app)

    response = client.get("/api/cadence/runs")
    assert response.status_code == 200
    assert response.json()["count"] == 4

    response = client.get("/api/cadence/runs", params={"job": "daily_scan"})
    assert response.status_code == 200
    assert response.json()["count"] == 2

    existing_id = response.json()["runs"][0]["id"]
    response = client.get(f"/api/cadence/runs/{existing_id}")
    assert response.status_code == 200
    assert response.json()["run"]["id"] == existing_id

    response = client.get("/api/cadence/runs/999999")
    assert response.status_code == 404

    print("PASS")