"""
services/staged/mcp_definition_history_pipeline_status_dashboard/contract.py

FastAPI contract for the MCP definition‑history pipeline‑status dashboard.
Mirrors the exemplar contract implementation and provides a runnable
self‑test that seeds an in‑memory SQLite database, invokes the endpoint,
and validates the response.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------------
# Real data‑layer imports – these must remain unchanged for production.
# ----------------------------------------------------------------------
from app.db import get_session  # pragma: no cover
from app.models import CadenceJobRun  # pragma: no cover

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------


class JobStatus(BaseModel):
    job: str = Field(..., description="Job identifier")
    status: str = Field(..., description="Current status of the job")
    started_at: datetime.datetime = Field(..., description="When the job started")
    finished_at: Optional[datetime.datetime] = Field(
        None, description="When the job finished (if applicable)"
    )
    rows_affected: int = Field(..., description="Number of rows affected by the job")


class PipelineStatusResponse(BaseModel):
    status: str = Field(..., description="Overall pipeline status")
    last_updated: datetime.datetime = Field(..., description="Timestamp of latest update")
    jobs: List[JobStatus] = Field(..., description="List of job status entries")


# ----------------------------------------------------------------------
# Router / endpoint
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/mcp/definition-history/pipeline-status",
    response_model=PipelineStatusResponse,
    tags=["mcp", "definition-history", "pipeline-status"],
)
def get_pipeline_status(db: Session = Depends(get_session)) -> PipelineStatusResponse:
    """
    Retrieve the current pipeline status for MCP definition‑history processing.
    """
    # Fetch all job runs ordered by start time (most recent last)
    stmt = select(CadenceJobRun).order_by(CadenceJobRun.started_at)
    job_rows = db.execute(stmt).scalars().all()

    # Build the list of job status objects
    jobs: List[JobStatus] = [
        JobStatus(
            job=row.job,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            rows_affected=row.rows_affected,
        )
        for row in job_rows
    ]

    # Determine overall pipeline status (simple heuristic)
    overall_status = "OK"
    for job in jobs:
        if job.status.lower() not in {"completed", "success", "ok"}:
            overall_status = "DEGRADED"
            break

    # Compute the most recent update timestamp
    if jobs:
        last_updated = max(job.started_at for job in jobs)
    else:
        last_updated = datetime.datetime.utcnow()

    return PipelineStatusResponse(
        status=overall_status,
        last_updated=last_updated,
        jobs=jobs,
    )


# ----------------------------------------------------------------------
# Self‑test (executed via `python -m services.staged.mcp_definition_history_pipeline_status_dashboard.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    # ------------------------------------------------------------------
    # Create an in‑memory SQLite engine that mimics the production DB
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = scoped_session(sessionmaker(bind=engine))

    # Create tables for the imported models
    # (app.models.Base is assumed to be the declarative base)
    from app.models import Base  # pragma: no cover

    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------
    # Dependency override to use the in‑memory session
    # ------------------------------------------------------------------
    def get_test_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed the test database with deterministic data
    # ------------------------------------------------------------------
    now = datetime.datetime.utcnow()
    earlier = now - datetime.timedelta(minutes=5)

    with SessionLocal() as db:
        db.add_all(
            [
                CadenceJobRun(
                    job="job_alpha",
                    status="completed",
                    started_at=earlier,
                    finished_at=earlier + datetime.timedelta(seconds=30),
                    rows_affected=100,
                ),
                CadenceJobRun(
                    job="job_beta",
                    status="running",
                    started_at=now,
                    finished_at=None,
                    rows_affected=0,
                ),
                CadenceJobRun(
                    job="job_gamma",
                    status="failed",
                    started_at=earlier,
                    finished_at=earlier + datetime.timedelta(seconds=45),
                    rows_affected=0,
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------
    # Execute the request against the test client
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/api/mcp/definition-history/pipeline-status")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert "jobs" in payload, "Response missing 'jobs' field"
    assert len(payload["jobs"]) == 3, f"Expected 3 jobs, got {len(payload['jobs'])}"
    statuses = {job["status"] for job in payload["jobs"]}
    assert "completed" in statuses, "Expected at least one job with status 'completed'"

    print("PASS")