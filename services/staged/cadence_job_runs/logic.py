# services/staged/cadence_job_runs/logic.py
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class RunOut(BaseModel):
    id: int
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    rows_affected: int | None = None
    detail: str | None = None

    class Config:
        orm_mode = True


class RunsResponse(BaseModel):
    runs: List[RunOut]


@router.get("/cadence/{job}/runs", response_model=RunsResponse)
def get_runs(
    job: str,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> RunsResponse:
    query = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.id)
        .offset(offset)
        .limit(limit)
    )
    runs = query.all()
    return RunsResponse(runs=runs)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build a temporary FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)

    # Create an in‑memory SQLite DB and initialise tables
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    test_session = SessionLocal()

    # Dependency override to use the test session
    def get_test_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = get_test_session

    # Seed a job with two runs
    job_name = "example_job"
    run1 = CadenceJobRun(
        job=job_name,
        status="completed",
        started_at=None,
        finished_at=None,
        rows_affected=10,
        detail="first run",
    )
    run2 = CadenceJobRun(
        job=job_name,
        status="failed",
        started_at=None,
        finished_at=None,
        rows_affected=5,
        detail="second run",
    )
    test_session.add_all([run1, run2])
    test_session.commit()

    # Exercise the endpoint
    client = TestClient(app)
    resp = client.get(f"/api/cadence/{job_name}/runs")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    runs = payload.get("runs", [])
    if len(runs) < 2:
        print("FAIL: expected at least 2 runs", file=sys.stderr)
        sys.exit(1)

    print("PASS")