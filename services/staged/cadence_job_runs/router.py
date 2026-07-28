"""services/staged/cadence_job_runs/router.py

Thin FastAPI router exposing cadence job run information.
"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

# Real data layer imports (must not be mocked here)
from app.db import get_session
from app.models import CadenceJobRun, Base  # type: ignore

# Business logic import (relative)
from .logic import get_cadence_job_runs

router = APIRouter(prefix="/api")


class RunOut(BaseModel):
    id: int
    status: str
    started_at: str | None
    finished_at: str | None
    rows_affected: int | None
    detail: str | None

    class Config:
        orm_mode = True


class RunsResponse(BaseModel):
    runs: List[RunOut]


@router.get(
    "/cadence/{job}/runs",
    response_model=RunsResponse,
    summary="Get paginated runs for a cadence job",
)
def read_cadence_job_runs(job: str, session: Session = Depends(get_session)):
    """Return all runs for the given cadence job."""
    runs = get_cadence_job_runs(job, session)
    return RunsResponse(runs=[RunOut.from_orm(r) for r in runs])


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ----------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB for the test (overriding the real dependency)
    # ----------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    TEST_DB_URL = "sqlite:///:memory:"
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    test_job_name = "test_job"
    with TestSessionLocal() as db:
        run1 = CadenceJobRun(
            job=test_job_name,
            status="completed",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:01:00Z",
            rows_affected=10,
            detail="first run",
        )
        run2 = CadenceJobRun(
            job=test_job_name,
            status="failed",
            started_at="2024-01-02T00:00:00Z",
            finished_at="2024-01-02T00:02:00Z",
            rows_affected=0,
            detail="second run",
        )
        db.add_all([run1, run2])
        db.commit()

    # Assemble FastAPI app
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # Perform request
    resp = client.get(f"/api/cadence/{test_job_name}/runs")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    data = resp.json()
    assert "runs" in data, "Response missing 'runs' key"
    assert isinstance(data["runs"], list), "'runs' is not a list"
    assert len(data["runs"]) == 2, f"Expected 2 runs, got {len(data['runs'])}"

    # Verify fields of the first run (order not guaranteed, so sort by id)
    runs_by_id = {run["id"]: run for run in data["runs"]}
    for run in (run1, run2):
        r = runs_by_id.get(run.id)
        assert r is not None, f"Run id {run.id} missing in response"
        assert r["status"] == run.status
        assert r["started_at"] == run.started_at
        assert r["finished_at"] == run.finished_at
        assert r["rows_affected"] == run.rows_affected
        assert r["detail"] == run.detail

    print("PASS")