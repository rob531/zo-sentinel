from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api")


class JobOutcomeMix(BaseModel):
    job: str
    total: int
    by_status: Dict[str, int]
    failed_share: float
    avg_rows_affected: float


class OutcomeMixTotals(BaseModel):
    runs: int
    jobs: int
    failed: int
    failed_share: float


class OutcomeMixResponse(BaseModel):
    jobs: List[JobOutcomeMix]
    totals: OutcomeMixTotals


def compute_outcome_mix(session: Session) -> Dict[str, Any]:
    rows = session.query(CadenceJobRun).all()

    aggregates: Dict[str, Dict[str, Any]] = {}
    total_runs = 0
    total_failed = 0

    for row in rows:
        job = str(row.job)
        status = "" if row.status is None else str(row.status)
        agg = aggregates.setdefault(
            job,
            {"total": 0, "by_status": {}, "failed": 0, "rows_affected": []},
        )
        agg["total"] += 1
        agg["by_status"][status] = agg["by_status"].get(status, 0) + 1
        if status.lower() == "failed":
            agg["failed"] += 1
            total_failed += 1
        if row.rows_affected is not None:
            agg["rows_affected"].append(row.rows_affected)
        total_runs += 1

    jobs: List[Dict[str, Any]] = []
    for job in sorted(aggregates):
        agg = aggregates[job]
        total = agg["total"]
        values = agg["rows_affected"]
        avg_rows = (sum(values) / len(values)) if values else 0.0
        jobs.append(
            {
                "job": job,
                "total": total,
                "by_status": agg["by_status"],
                "failed_share": (agg["failed"] / total) if total else 0.0,
                "avg_rows_affected": float(avg_rows),
            }
        )

    return {
        "jobs": jobs,
        "totals": {
            "runs": total_runs,
            "jobs": len(aggregates),
            "failed": total_failed,
            "failed_share": (total_failed / total_runs) if total_runs else 0.0,
        },
    }


@router.get("/cadence/jobs/outcome-mix", response_model=OutcomeMixResponse)
def cadence_job_outcome_mix(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return compute_outcome_mix(session)


app = FastAPI(title="cadence_job_outcome_mix_api")
app.include_router(router)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    db = SessionLocal()
    db.add_all(
        [
            CadenceJobRun(job="job_a", status="success", rows_affected=10, started_at=datetime.utcnow()),
            CadenceJobRun(job="job_a", status="failed", rows_affected=0, started_at=datetime.utcnow()),
            CadenceJobRun(job="job_b", status="success", rows_affected=5, started_at=datetime.utcnow()),
            CadenceJobRun(job="job_c", status="failed", rows_affected=2, started_at=datetime.utcnow()),
        ]
    )
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/api/cadence/jobs/outcome-mix")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert any(job["job"] == "job_a" for job in payload["jobs"])
    job_a = next(job for job in payload["jobs"] if job["job"] == "job_a")
    assert job_a["by_status"].get("success") == 1
    assert job_a["by_status"].get("failed") == 1
    print("PASS")