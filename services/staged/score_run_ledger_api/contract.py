import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# Real data layer imports
from app.db import get_session
from app.models import CadenceJobRun, Base

router = APIRouter(prefix="/api")


class RunOut(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime.datetime
    finished_at: Optional[datetime.datetime] = None
    duration_seconds: Optional[int] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None

    class Config:
        orm_mode = True


class RunsResponse(BaseModel):
    runs: List[RunOut]
    total: int
    by_status: Dict[str, int]


@router.get("/scores/runs", response_model=RunsResponse)
def get_score_runs(
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
):
    total = session.query(func.count(CadenceJobRun.id)).scalar()

    runs_q = (
        session.query(CadenceJobRun)
        .order_by(CadenceJobRun.started_at.desc())
        .limit(limit)
        .all()
    )

    status_counts = (
        session.query(CadenceJobRun.status, func.count(CadenceJobRun.id))
        .group_by(CadenceJobRun.status)
        .all()
    )
    by_status = {status: count for status, count in status_counts}

    runs_out: List[RunOut] = []
    for r in runs_q:
        duration = None
        if r.started_at is not None and r.finished_at is not None:
            delta = r.finished_at - r.started_at
            duration = int(delta.total_seconds())
        runs_out.append(
            RunOut(
                id=r.id,
                job=r.job,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_seconds=duration,
                rows_affected=r.rows_affected,
                detail=r.detail,
            )
        )

    return RunsResponse(runs=runs_out, total=total, by_status=by_status)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _get_test_session() -> Session:
    """Create an in‑memory SQLite session bound to the real models."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return TestSession()


def _seed_test_data(session: Session) -> None:
    now = datetime.datetime.utcnow()
    completed = CadenceJobRun(
        id=1,
        job="score_job",
        status="completed",
        started_at=now - datetime.timedelta(seconds=5),
        finished_at=now,
        rows_affected=10,
        detail="ok",
    )
    failed = CadenceJobRun(
        id=2,
        job="score_job",
        status="failed",
        started_at=now - datetime.timedelta(seconds=10),
        finished_at=None,
        rows_affected=0,
        detail="error",
    )
    running = CadenceJobRun(
        id=3,
        job="score_job",
        status="running",
        started_at=now - datetime.timedelta(seconds=2),
        finished_at=None,
        rows_affected=None,
        detail=None,
    )
    session.add_all([completed, failed, running])
    session.commit()


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    test_session = _get_test_session()
    _seed_test_data(test_session)
    app.dependency_overrides[get_session] = lambda: test_session

    client = TestClient(app)
    resp = client.get("/api/scores/runs?limit=50")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total"] == 3, f"total {data['total']}"
    by_status = data["by_status"]
    assert by_status.get("failed") == 1, f"failed count {by_status.get('failed')}"
    assert by_status.get("running") == 1, f"running count {by_status.get('running')}"

    completed_run = next(r for r in data["runs"] if r["status"] == "completed")
    assert completed_run["duration_seconds"] == 5, f"duration {completed_run['duration_seconds']}"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()