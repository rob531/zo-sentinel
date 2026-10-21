from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_job_failure_rollup
except ImportError:
    try:
        from .logic import get_cadence_job_failure_rollup as get_job_failure_rollup
    except ImportError:
        from .logic import get_failure_rollup as get_job_failure_rollup


router = APIRouter()


class JobFailureRollupItem(BaseModel):
    job: str
    runs: int
    failed: int
    failure_pct: float
    last_run_at: Optional[datetime]
    last_duration_seconds: Optional[float]
    rows_affected: int
    last_error: Optional[str]


class JobFailureRollupResponse(BaseModel):
    jobs: List[JobFailureRollupItem]
    generated_at: datetime
    total_jobs: int


@router.get(
    "/api/cadence/job-failure-rollup",
    response_model=JobFailureRollupResponse,
)
def read_job_failure_rollup(
    session: Session = Depends(get_session),
    job: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=3650),
) -> JobFailureRollupResponse:
    payload = get_job_failure_rollup(session, job=job, days=days)
    return JobFailureRollupResponse.model_validate(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    that_app = FastAPI()
    that_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="mcp_scanner",
                    status="success",
                    started_at=now,
                    finished_at=now,
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    job="mcp_scanner",
                    status="success",
                    started_at=now,
                    finished_at=now,
                    rows_affected=20,
                    detail={},
                ),
                CadenceJobRun(
                    job="mcp_scanner",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    rows_affected=5,
                    detail={"error": "scanner failed"},
                ),
                CadenceJobRun(
                    job="signal_analyser",
                    status="success",
                    started_at=now,
                    finished_at=now,
                    rows_affected=7,
                    detail={},
                ),
            ]
        )
        db.commit()

    response = TestClient(that_app).get("/api/cadence/job-failure-rollup")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_jobs"] == 2, payload
    mcp_scanner = next(item for item in payload["jobs"] if item["job"] == "mcp_scanner")
    assert mcp_scanner["runs"] == 3, payload
    assert mcp_scanner["failed"] == 1, payload
    assert mcp_scanner["failure_pct"] > 0, payload
    print("PASS")