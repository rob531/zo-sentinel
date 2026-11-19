from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

try:
    from .logic import get_cadence_failures
except ImportError:
    get_cadence_failures = None


router = APIRouter(prefix="/api", tags=["cadence"])


class CadenceFailureJobResponse(BaseModel):
    job: str
    failures: int
    last_status: str | None
    last_started_at: str | None
    last_detail: dict[str, Any] | None
    rows_affected: int


class CadenceFailureWindowResponse(BaseModel):
    hours: int
    jobs: list[CadenceFailureJobResponse]
    total_failures: int


def _detail_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _query_cadence_failures(db: Session, hours: int) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    statement = (
        select(CadenceJobRun)
        .where(
            CadenceJobRun.started_at >= cutoff,
            func.lower(CadenceJobRun.status).notin_(("ok", "success")),
        )
        .order_by(CadenceJobRun.started_at.desc())
    )
    rows = db.execute(statement).scalars().all()

    grouped: dict[str, list[CadenceJobRun]] = {}
    for row in rows:
        grouped.setdefault(str(row.job), []).append(row)

    jobs: list[dict[str, Any]] = []
    for job, job_rows in grouped.items():
        latest = job_rows[0]
        started_at = latest.started_at
        jobs.append(
            {
                "job": job,
                "failures": len(job_rows),
                "last_status": latest.status,
                "last_started_at": (
                    started_at.isoformat()
                    if hasattr(started_at, "isoformat")
                    else str(started_at)
                    if started_at is not None
                    else None
                ),
                "last_detail": _detail_value(latest.detail),
                "rows_affected": sum(
                    int(row.rows_affected or 0) for row in job_rows
                ),
            }
        )

    jobs.sort(key=lambda item: item["last_started_at"] or "", reverse=True)
    return {
        "hours": hours,
        "jobs": jobs,
        "total_failures": len(rows),
    }


@router.get(
    "/cadence/failures",
    response_model=CadenceFailureWindowResponse,
)
def cadence_failures(
    hours: int = Query(24, ge=1),
    db: Session = Depends(get_session),
) -> CadenceFailureWindowResponse:
    if get_cadence_failures is not None:
        result = get_cadence_failures(db, hours)
    else:
        result = _query_cadence_failures(db, hours)

    if isinstance(result, CadenceFailureWindowResponse):
        return result
    return CadenceFailureWindowResponse.parse_obj(result)


if __name__ == "__main__":
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
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="alpha",
                    status="ERROR",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=1),
                    rows_affected=2,
                    detail={"message": "first"},
                ),
                CadenceJobRun(
                    id=2,
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2),
                    rows_affected=3,
                    detail={"message": "second"},
                ),
                CadenceJobRun(
                    id=3,
                    job="beta",
                    status="Error",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3),
                    rows_affected=4,
                    detail={"message": "third"},
                ),
                CadenceJobRun(
                    id=4,
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4),
                    rows_affected=10,
                    detail=None,
                ),
                CadenceJobRun(
                    id=5,
                    job="beta",
                    status="error",
                    started_at=now - timedelta(hours=48),
                    finished_at=now - timedelta(hours=48),
                    rows_affected=99,
                    detail={"message": "old"},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/cadence/failures?hours=24")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_failures"] == 3
    assert len(payload["jobs"]) == 2
    assert all(job["job"] != "alpha" or job["failures"] == 2 for job in payload["jobs"])
    assert all(job["job"] != "beta" or job["failures"] == 1 for job in payload["jobs"])
    assert sum(job["rows_affected"] for job in payload["jobs"]) == 9
    print("PASS")