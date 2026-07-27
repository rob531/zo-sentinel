# services/staged/cadence_job_sla_report/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_job_sla_report

router = APIRouter(prefix="/api", tags=["cadence_job_sla_report"])


@router.get("/cadence/jobs/sla")
def cadence_job_sla_report(session: Session = Depends(get_session)):
    """
    Thin wrapper that delegates the heavy lifting to the logic layer.
    """
    return get_cadence_job_sla_report(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, CadenceJobRun  # type: ignore

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed the DB with four jobs, one of which violates the SLA
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    runs = [
        # heartbeat job – fast, successful
        CadenceJobRun(
            job="heartbeat_job",
            status="success",
            started_at=now - datetime.timedelta(seconds=100),
            finished_at=now,
            rows_affected=10,
            detail="ok",
        ),
        # heartbeat job – failed (SLA violation)
        CadenceJobRun(
            job="heartbeat_job",
            status="failed",
            started_at=now - datetime.timedelta(seconds=400),
            finished_at=now - datetime.timedelta(seconds=200),
            rows_affected=0,
            detail="fail",
        ),
        # scanner job – long but within SLA
        CadenceJobRun(
            job="scanner_job",
            status="success",
            started_at=now - datetime.timedelta(seconds=2000),
            finished_at=now - datetime.timedelta(seconds=100),
            rows_affected=5,
            detail="ok",
        ),
        # other job – quick success
        CadenceJobRun(
            job="other_job",
            status="success",
            started_at=now - datetime.timedelta(seconds=50),
            finished_at=now,
            rows_affected=3,
            detail="ok",
        ),
    ]

    with SessionLocal() as db:
        db.add_all(runs)
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app, include router, and apply the dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request and validate response
    # ------------------------------------------------------------------- #
    response = client.get("/api/cadence/jobs/sla")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    # Normalise payload shape (expecting a dict with a 'jobs' key)
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    assert isinstance(jobs, list), "Response does not contain a list of jobs"

    sla_violated = [j for j in jobs if j.get("sla_violated")]
    assert len(sla_violated) == 1, f"Expected exactly one SLA violation, got {len(sla_violated)}"

    # Ensure percentile fields are numeric
    for job in jobs:
        assert isinstance(job.get("p50_ms"), (int, float)), "p50_ms is not numeric"
        assert isinstance(job.get("p95_ms"), (int, float)), "p95_ms is not numeric"

    print("PASS")