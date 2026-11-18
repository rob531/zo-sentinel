from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _duration_seconds(
    started_at: Optional[datetime], finished_at: Optional[datetime]
) -> Optional[float]:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def _run_to_dict(run: CadenceJobRun, include_detail: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "id": run.id,
        "job": run.job,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": _duration_seconds(run.started_at, run.finished_at),
        "rows_affected": run.rows_affected,
    }
    if include_detail:
        result["detail"] = run.detail
    return result


def list_runs(
    db: Session,
    job: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return recent cadence ledger runs, newest first."""
    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 100
    requested_limit = max(1, min(requested_limit, 500))

    query = select(CadenceJobRun)
    if job is not None:
        query = query.where(CadenceJobRun.job == job)
    if status is not None:
        query = query.where(CadenceJobRun.status == status)
    query = query.order_by(
        CadenceJobRun.started_at.desc(), CadenceJobRun.id.desc()
    ).limit(requested_limit)

    runs = db.execute(query).scalars().all()
    return [_run_to_dict(run) for run in runs]


def get_run(db: Session, run_id: int) -> Optional[Dict[str, Any]]:
    """Return one cadence ledger run by its identifier."""
    run = db.get(CadenceJobRun, run_id)
    if run is None:
        return None
    return _run_to_dict(run, include_detail=True)


# Descriptive aliases retained for callers that use service-specific names.
list_cadence_runs = list_runs
get_cadence_run = get_run


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/cadence/runs")
    def test_list_runs(
        job: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        db: Session = Depends(get_session),
    ):
        runs = list_runs(db, job=job, status=status, limit=limit)
        return {"count": len(runs), "runs": runs}

    @test_app.get("/api/cadence/runs/{run_id}")
    def test_get_run(run_id: int, db: Session = Depends(get_session)):
        result = get_run(db, run_id)
        if result is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Run not found")
        return result

    test_app.dependency_overrides[get_session] = override_get_session

    started = datetime(2024, 1, 1, 12, 0, 0)
    with TestingSessionLocal() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="daily_scan",
                    status="ok",
                    started_at=started,
                    finished_at=started + timedelta(seconds=3),
                    rows_affected=10,
                    detail={"message": "completed"},
                ),
                CadenceJobRun(
                    job="weekly_scan",
                    status="ok",
                    started_at=started + timedelta(hours=1),
                    finished_at=started + timedelta(hours=1, seconds=5),
                    rows_affected=20,
                    detail={"message": "completed"},
                ),
                CadenceJobRun(
                    job="daily_scan",
                    status="failed",
                    started_at=started + timedelta(hours=2),
                    finished_at=started + timedelta(hours=2, seconds=2),
                    rows_affected=0,
                    detail={"error": "scan failed"},
                ),
            ]
        )
        db.commit()

    client = TestClient(test_app)
    response = client.get("/api/cadence/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert len(payload["runs"]) == 3

    failed = client.get("/api/cadence/runs", params={"status": "failed"})
    assert failed.status_code == 200
    assert failed.json()["count"] == 1
    assert failed.json()["runs"][0]["status"] == "failed"

    run_id = payload["runs"][0]["id"]
    detail = client.get(f"/api/cadence/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id
    assert detail.json()["duration_seconds"] is not None
    assert detail.json()["duration_seconds"] >= 0

    print("PASS")