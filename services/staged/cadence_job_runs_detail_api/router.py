from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any, Optional

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_cadence_job_runs_detail

router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceJobRunResponse(BaseModel):
    id: int
    job: str
    status: str
    started_at: Any
    finished_at: Optional[Any] = None
    duration_seconds: Optional[float] = None
    rows_affected: Optional[int] = None
    detail: Any = None


class CadenceJobRunsDetailResponse(BaseModel):
    jobs: list[str]
    runs: list[CadenceJobRunResponse]


@router.get(
    "/cadence/runs/detail",
    response_model=CadenceJobRunsDetailResponse,
)
def cadence_job_runs_detail(
    job: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> CadenceJobRunsDetailResponse:
    result = get_cadence_job_runs_detail(session, job=job, limit=limit)
    return CadenceJobRunsDetailResponse(**result)


if __name__ == "__main__":
    from datetime import datetime, timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    started_at = datetime(2024, 1, 1, 12, 0, 0)
    with TestSession() as seed_session:
        seed_session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="completed",
                    started_at=started_at,
                    finished_at=started_at + timedelta(seconds=5),
                    rows_affected=10,
                    detail={"source": "test"},
                ),
                CadenceJobRun(
                    job="alpha",
                    status="running",
                    started_at=started_at + timedelta(minutes=1),
                    finished_at=None,
                    rows_affected=0,
                    detail={"source": "test"},
                ),
                CadenceJobRun(
                    job="beta",
                    status="completed",
                    started_at=started_at + timedelta(minutes=2),
                    finished_at=started_at + timedelta(minutes=2, seconds=8),
                    rows_affected=20,
                    detail={"source": "test"},
                ),
            ]
        )
        seed_session.commit()

    response = TestClient(test_app).get("/api/cadence/runs/detail")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["runs"]) == 3
    assert len(set(payload["jobs"])) == 2
    assert any(
        run["finished_at"] is None and run["duration_seconds"] is None
        for run in payload["runs"]
    )
    print("PASS")