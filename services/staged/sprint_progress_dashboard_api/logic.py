from datetime import datetime, timedelta
from typing import Optional

from app.db import Base, get_session
from app.models import CadenceJobRun
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


router = APIRouter(prefix="/api", tags=["sprint"])


class RunByDay(BaseModel):
    date: str
    rows: int
    status: str


class SprintProgressResponse(BaseModel):
    days: int
    total_runs: int
    total_rows: int
    pass_rate: float
    error_rate: float
    rows_per_run_avg: float
    on_track: bool
    runs_by_day: list[RunByDay]


def get_sprint_progress(days: int = 7, db: Session = Depends(get_session)) -> SprintProgressResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    runs = db.query(CadenceJobRun).filter(
        CadenceJobRun.job == "scoring",
        CadenceJobRun.finished_at >= cutoff,
    ).all()

    total_runs = len(runs)
    total_rows = sum(r.rows_affected or 0 for r in runs)
    rows_per_run_avg = total_rows / total_runs if total_runs > 0 else 0.0

    pass_count = sum(1 for r in runs if r.status == "pass")
    error_count = sum(1 for r in runs if r.status == "error")

    pass_rate = pass_count / total_runs if total_runs > 0 else 0.0
    error_rate = error_count / total_runs if total_runs > 0 else 0.0

    on_track = pass_rate >= 0.95

    runs_by_day = []
    for r in runs:
        date_str = r.finished_at.strftime("%Y-%m-%d") if r.finished_at else ""
        runs_by_day.append(RunByDay(date=date_str, rows=r.rows_affected or 0, status=r.status))

    return SprintProgressResponse(
        days=days,
        total_runs=total_runs,
        total_rows=total_rows,
        pass_rate=round(pass_rate, 4),
        error_rate=round(error_rate, 4),
        rows_per_run_avg=round(rows_per_run_avg, 4),
        on_track=on_track,
        runs_by_day=runs_by_day,
    )


@router.get("/sprint/progress", response_model=SprintProgressResponse)
def sprint_progress(days: int = 7, db: Session = Depends(get_session)) -> SprintProgressResponse:
    return get_sprint_progress(days, db)


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    base_date = datetime.utcnow() - timedelta(days=2)
    seed_data = [
        CadenceJobRun(job="scoring", status="pass", rows_affected=100, finished_at=base_date, started_at=base_date, detail="ok"),
        CadenceJobRun(job="scoring", status="fail", rows_affected=50, finished_at=base_date, started_at=base_date, detail="fail"),
        CadenceJobRun(job="scoring", status="error", rows_affected=75, finished_at=base_date + timedelta(days=1), started_at=base_date, detail="err"),
        CadenceJobRun(job="scoring", status="pass", rows_affected=120, finished_at=base_date + timedelta(days=1), started_at=base_date, detail="ok"),
        CadenceJobRun(job="scoring", status="error", rows_affected=80, finished_at=base_date + timedelta(days=2), started_at=base_date, detail="err"),
    ]
    for r in seed_data:
        db.add(r)
    db.commit()

    def override_get_session():
        try:
            yield db
        finally:
            pass

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient
    client = TestClient(app)
    app.dependency_overrides[get_session] = override_get_session

    response = client.get(f"/api/sprint/progress?days={7}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["days"] == 7
    assert data["total_runs"] == 5
    assert data["total_rows"] == 425
    assert data["pass_rate"] == 0.4
    assert data["error_rate"] == 0.4
    assert data["rows_per_run_avg"] == 85.0
    assert data["on_track"] == (data["pass_rate"] >= 0.95)

    db.close()
    print("PASS")