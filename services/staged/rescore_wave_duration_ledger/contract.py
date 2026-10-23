#!/usr/bin/env python
"""
services.staged.rescore_wave_duration_ledger.contract
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class WaveDuration(BaseModel):
    job: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    rows_affected: int


class WaveDurationResponse(BaseModel):
    waves: List[WaveDuration]


@router.get(
    "/scoring/wave-durations",
    response_model=WaveDurationResponse,
    tags=["scoring"],
    summary="Get wave duration ledger for scoring / rescore jobs",
)
def get_wave_durations(db: Session = Depends(get_session)) -> WaveDurationResponse:
    """
    Return a list of completed scoring or rescore CadenceJobRun entries with
    their execution duration in seconds.
    """
    runs = (
        db.query(CadenceJobRun)
        .filter(
            or_(
                CadenceJobRun.job.ilike("score%"),
                CadenceJobRun.job.ilike("rescore%"),
            ),
            CadenceJobRun.status == "completed",
        )
        .all()
    )

    waves = []
    for run in runs:
        if run.started_at and run.finished_at:
            duration = (run.finished_at - run.started_at).total_seconds()
        else:
            duration = 0.0
        waves.append(
            WaveDuration(
                job=run.job,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_seconds=duration,
                rows_affected=run.rows_affected or 0,
            )
        )
    return WaveDurationResponse(waves=waves)


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.rescore_wave_duration_ledger.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from datetime import timedelta

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (StaticPool ensures a single connection)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool").pool.StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Dependency override providing the test session
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed deterministic data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    seed_data = [
        CadenceJobRun(
            job="score_wave_1",
            status="completed",
            started_at=now - timedelta(seconds=120),
            finished_at=now - timedelta(seconds=60),
            rows_affected=10,
            detail="",
        ),
        CadenceJobRun(
            job="rescore_wave_2",
            status="completed",
            started_at=now - timedelta(seconds=300),
            finished_at=now - timedelta(seconds=150),
            rows_affected=20,
            detail="",
        ),
        CadenceJobRun(
            job="other_job",
            status="completed",
            started_at=now - timedelta(seconds=400),
            finished_at=now - timedelta(seconds=350),
            rows_affected=5,
            detail="",
        ),
    ]

    with TestSession() as s:
        s.add_all(seed_data)
        s.commit()

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/scoring/wave-durations")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    waves = data.get("waves", [])
    if len(waves) < 1:
        print("FAIL: no waves returned")
        sys.exit(1)

    # Verify that the known duration for the first seeded job is present
    expected_duration = 60.0  # 120s - 60s
    durations = [w["duration_seconds"] for w in waves]
    if expected_duration not in durations:
        print("FAIL: expected duration not found")
        sys.exit(1)

    print("PASS")
    sys.exit(0)