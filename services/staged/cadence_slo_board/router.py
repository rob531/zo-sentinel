"""router.py -- the service's HTTP surface (one concern, one file).

Thin router exposing the cadence SLO board endpoint. Injects the real
SQLAlchemy Session via Depends(get_session) and delegates all work to
logic.py. Relative imports keep the module usable in both staged and
active service directories.
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic module contains the actual computation.
# It must expose a function `get_cadence_slo(db: Session, hours: int) -> List[dict]`.
from .logic import get_cadence_slo

router = APIRouter(prefix="/api", tags=["cadence_slo_board"])


class JobSLO(BaseModel):
    job: str
    runs: int
    failures: int
    success_rate: float = Field(..., ge=0.0, le=1.0)
    rows_affected: int
    mean_duration_seconds: float


class SLOResponse(BaseModel):
    hours: int
    jobs: List[JobSLO]


@router.get("/cadence/slo", response_model=SLOResponse)
def get_slo(
    hours: int = 24,
    db: Session = Depends(get_session),
) -> SLOResponse:
    """Return per‑job SLO statistics for the last *hours* hours."""
    jobs = get_cadence_slo(db, hours)
    return SLOResponse(hours=hours, jobs=jobs)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, CadenceJobRun

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    test_session = SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed data: two jobs, three runs (2 ok, 1 failed)
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    runs = [
        CadenceJobRun(
            id=1,
            job="jobA",
            status="ok",
            started_at=now,
            finished_at=now.replace(microsecond=0),
            rows_affected=10,
            detail="run 1",
        ),
        CadenceJobRun(
            id=2,
            job="jobA",
            status="ok",
            started_at=now,
            finished_at=now.replace(microsecond=0),
            rows_affected=20,
            detail="run 2",
        ),
        CadenceJobRun(
            id=3,
            job="jobB",
            status="failed",
            started_at=now,
            finished_at=now.replace(microsecond=0),
            rows_affected=5,
            detail="run 3",
        ),
    ]
    test_session.add_all(runs)
    test_session.commit()

    # ------------------------------------------------------------------- #
    # Dependency override to inject the test session
    # ------------------------------------------------------------------- #
    def get_test_session():
        try:
            yield test_session
        finally:
            pass

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request and assertions
    # ------------------------------------------------------------------- #
    response = client.get("/api/cadence/slo?hours=24")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    data = response.json()
    assert data["hours"] == 24
    assert isinstance(data["jobs"], list)
    assert len(data["jobs"]) == 2, f"Expected 2 jobs, got {len(data['jobs'])}"

    # Find the failing job
    failing_job = next((j for j in data["jobs"] if j["job"] == "jobB"), None)
    assert failing_job is not None, "jobB not found in response"
    assert failing_job["failures"] == 1, f"Expected 1 failure, got {failing_job['failures']}"
    assert failing_job["success_rate"] == 0.0, f"Expected success_rate 0.0, got {failing_job['success_rate']}"
    assert failing_job["rows_affected"] == 5, f"Rows affected mismatch: {failing_job['rows_affected']}"

    # Verify jobA aggregates
    job_a = next((j for j in data["jobs"] if j["job"] == "jobA"), None)
    assert job_a is not None, "jobA not found in response"
    assert job_a["runs"] == 2
    assert job_a["failures"] == 0
    assert job_a["success_rate"] == 1.0
    assert job_a["rows_affected"] == 30

    print("PASS")
    sys.exit(0)