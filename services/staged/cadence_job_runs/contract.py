"""
services/staged/cadence_job_runs/contract.py

FastAPI contract for retrieving cadence job runs.
Mirrors the exemplar contract implementation.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import Base, CadenceJobRun  # type: ignore

router = APIRouter(prefix="/api")


class RunOut(BaseModel):
    id: int
    status: str
    started_at: datetime.datetime
    finished_at: Optional[datetime.datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None

    class Config:
        orm_mode = True


@router.get("/cadence/{job}/runs", response_model=dict)
def get_cadence_job_runs(
    job: str,
    db: Session = Depends(get_session),
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Return paginated runs for a given cadence job."""
    runs: List[CadenceJobRun] = (
        db.query(CadenceJobRun)
        .filter(CadenceJobRun.job == job)
        .order_by(CadenceJobRun.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"runs": [RunOut.from_orm(r) for r in runs]}


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.cadence_job_runs.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build a throwaway SQLite DB and seed it with test data
    # ------------------------------------------------------------------- #
    TEST_DB_URL = "sqlite:///:memory:"
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    # Seed data
    with TestSessionLocal() as sess:
        run1 = CadenceJobRun(
            job="test_job",
            status="completed",
            started_at=datetime.datetime(2023, 1, 1, 12, 0, 0),
            finished_at=datetime.datetime(2023, 1, 1, 12, 5, 0),
            rows_affected=10,
            detail="first run",
        )
        run2 = CadenceJobRun(
            job="test_job",
            status="failed",
            started_at=datetime.datetime(2023, 1, 2, 13, 0, 0),
            finished_at=datetime.datetime(2023, 1, 2, 13, 2, 0),
            rows_affected=0,
            detail="second run",
        )
        sess.add_all([run1, run2])
        sess.commit()

    # ------------------------------------------------------------------- #
    # Dependency override to use the test session
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/cadence/test_job/runs")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "runs" in data, "Missing 'runs' key in response"
    runs = data["runs"]
    assert isinstance(runs, list), "'runs' is not a list"
    assert len(runs) == 2, f"Expected 2 runs, got {len(runs)}"

    # Basic field checks
    ids = {run["id"] for run in runs}
    assert ids == {run1.id, run2.id}, "Run IDs do not match seeded data"

    print("PASS")