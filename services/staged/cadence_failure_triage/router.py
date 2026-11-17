"""router.py -- the service's HTTP surface (one concern, one file).

Thin APIRouter exposing `router`. Delegates to logic.py, injecting a real
SQLAlchemy Session via Depends(get_session). The module works both in the
staged and active service directories because intra‑service imports are
relative.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cadence_failures

router = APIRouter(prefix="/api/cadence", tags=["cadence_failure_triage"])


class Failure(BaseModel):
    id: int
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    rows_affected: Optional[int] = None
    error_hint: Optional[str] = None


class Summary(BaseModel):
    failed_runs: int
    distinct_jobs: int
    worst_job: Optional[str] = None
    total_duration_seconds: float


class FailureResponse(BaseModel):
    days: int
    generated_at: datetime
    failures: List[Failure]
    summary: Summary


@router.get("/failures", response_model=FailureResponse)
def get_failures(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> FailureResponse:
    """Return a triage view of recent Cadence job failures."""
    return get_cadence_failures(db, days)


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import CadenceJobRun, Base  # type: ignore

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    runs = [
        CadenceJobRun(
            id=1,
            job="daily_sync",
            status="failed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=55),
            rows_affected=10,
            detail=json.dumps({"error": "Connection timeout after 30s"}),
        ),
        CadenceJobRun(
            id=2,
            job="daily_sync",
            status="failed",
            started_at=now - timedelta(hours=1, minutes=30),
            finished_at=now - timedelta(hours=1, minutes=25),
            rows_affected=5,
            detail=json.dumps({"exception": "IntegrityError: duplicate key"}),
        ),
        CadenceJobRun(
            id=3,
            job="daily_sync",
            status="success",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=50),
            rows_affected=20,
            detail=json.dumps({}),
        ),
        CadenceJobRun(
            id=4,
            job="daily_sync",
            status="running",
            started_at=now - timedelta(minutes=10),
            finished_at=None,
            rows_affected=None,
            detail=json.dumps({}),
        ),
    ]

    with SessionLocal() as sess:
        sess.add_all(runs)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Exercise endpoint
    # ------------------------------------------------------------------- #
    resp = client.get("/api/cadence/failures?days=7")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # ------------------------------------------------------------------- #
    # Assertions per contract
    # ------------------------------------------------------------------- #
    assert data["summary"]["failed_runs"] == 2, "expected two failed runs"
    assert data["summary"]["distinct_jobs"] == 1, "expected one distinct job"
    # one of the failures must contain the seeded error message
    error_messages = [
        f["error_hint"] for f in data["failures"] if f["error_hint"] is not None
    ]
    assert any(
        "Connection timeout" in msg for msg in error_messages
    ), "expected seeded error message"
    # ensure no success row appears
    assert all(f["status"] != "success" for f in data["failures"]), "success row leaked"

    print("PASS")