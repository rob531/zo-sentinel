# services/staged/cadence_sprint_progress/contract.py
from datetime import datetime
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_session, Base  # real data layer
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


@router.get("/cadence/sprint-progress")
def get_sprint_progress(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Return the most recent sprint (or wave) progress.
    """
    runs: List[CadenceJobRun] = (
        db.query(CadenceJobRun)
        .filter(
            or_(
                CadenceJobRun.job.like("sprint%"),
                CadenceJobRun.job.like("wave%"),
            )
        )
        .all()
    )
    if not runs:
        raise HTTPException(status_code=404, detail="No sprint data found")

    # Determine the latest date (by started_at) across all runs
    latest_date = max(run.started_at.date() for run in runs)

    # Keep only runs that belong to the latest date
    latest_runs = [run for run in runs if run.started_at.date() == latest_date]

    jobs = [
        {
            "job": run.job,
            "status": run.status,
            "rows_affected": run.rows_affected,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        for run in latest_runs
    ]

    current_sprint = {
        "name": f"Sprint {latest_date.isoformat()}",
        "started_at": latest_date.isoformat(),
        "jobs": jobs,
    }

    return {"current_sprint": current_sprint}


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.cadence_sprint_progress.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    # ------------------------------------------------------------------- #
    test_app = FastAPI()
    test_app.include_router(router)

    # In‑memory SQLite engine (StaticPool makes it behave like a singleton)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override to use the in‑memory session
    def override_get_session() -> Session:  # type: ignore
        return TestSession()

    test_app.dependency_overrides[get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data: 4 rows, 2 job names, 2 dates
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    yesterday = now.replace(day=now.day - 1)

    seed_data = [
        CadenceJobRun(
            job="sprint_alpha",
            status="running",
            rows_affected=10,
            started_at=now,
            finished_at=None,
            detail="first sprint today",
        ),
        CadenceJobRun(
            job="sprint_alpha",
            status="completed",
            rows_affected=20,
            started_at=now,
            finished_at=now,
            detail="second sprint today",
        ),
        CadenceJobRun(
            job="wave_beta",
            status="failed",
            rows_affected=5,
            started_at=yesterday,
            finished_at=yesterday,
            detail="wave yesterday",
        ),
        CadenceJobRun(
            job="wave_beta",
            status="running",
            rows_affected=7,
            started_at=yesterday,
            finished_at=None,
            detail="wave yesterday ongoing",
        ),
    ]

    with TestSession() as sess:
        sess.add_all(seed_data)
        sess.commit()

    # ------------------------------------------------------------------- #
    # Execute request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(test_app)
    response = client.get("/api/cadence/sprint-progress")
    if response.status_code != 200:
        print(f"FAIL: Expected 200, got {response.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = response.json()
    if "current_sprint" not in payload:
        print("FAIL: 'current_sprint' key missing", file=sys.stderr)
        sys.exit(1)

    if not isinstance(payload["current_sprint"].get("jobs"), list) or len(
        payload["current_sprint"]["jobs"]
    ) == 0:
        print("FAIL: No job entries in current_sprint", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)