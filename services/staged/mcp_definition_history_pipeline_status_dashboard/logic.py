# services/staged/mcp_definition_history_pipeline_status_dashboard/logic.py
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, CadenceJobRun, McpDefinitionHistory

router = APIRouter(prefix="/api")


# ---------- Pydantic schemas ----------
class JobInfo(BaseModel):
    job: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None


class PipelineStatusResponse(BaseModel):
    status: str
    last_updated: datetime
    jobs: List[JobInfo] = Field(default_factory=list)


# ---------- Core logic ----------
def _aggregate_status(jobs: List[CadenceJobRun]) -> str:
    """Derive an overall pipeline status from individual job records."""
    if not jobs:
        return "unknown"
    # If any job is still running or pending, pipeline is considered running.
    for job in jobs:
        if job.status.lower() not in {"completed", "success", "succeeded"}:
            return "running"
    return "completed"


@router.get(
    "/mcp/definition-history/pipeline-status",
    response_model=PipelineStatusResponse,
    name="mcp_definition_history_pipeline_status_dashboard:get_status",
)
def get_pipeline_status(session: Session = Depends(get_session)):
    # Fetch the most recent definition‑history record.
    recent_def = (
        session.query(McpDefinitionHistory)
        .order_by(desc(McpDefinitionHistory.created_at))
        .first()
    )
    if not recent_def:
        raise HTTPException(status_code=404, detail="No definition history found")

    # Pull all job runs linked to this definition‑history entry.
    job_runs = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.definition_history_id == recent_def.id)
        .order_by(CadenceJobRun.started_at)
        .all()
    )

    # Build the response payload.
    jobs_payload = [
        JobInfo(
            job=jr.job_name,
            status=jr.status,
            started_at=jr.started_at,
            finished_at=jr.finished_at,
            rows_affected=jr.rows_affected,
        )
        for jr in job_runs
    ]

    overall_status = _aggregate_status(job_runs)

    return PipelineStatusResponse(
        status=overall_status,
        last_updated=recent_def.created_at,
        jobs=jobs_payload,
    )


# ---------- Self‑test ----------
if __name__ == "__main__":
    import os

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a throw‑away SQLite DB and bind it to the app's session dependency.
    SQLITE_URL = "sqlite+pysqlite:///:memory:"
    engine = create_engine(SQLITE_URL, echo=False, future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Re‑create all tables.
    Base.metadata.create_all(bind=engine)

    # Dependency override.
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data.
    with TestingSessionLocal() as db:
        # Two definition‑history rows.
        dh1 = McpDefinitionHistory(created_at=datetime(2023, 1, 1, 12, 0, 0))
        dh2 = McpDefinitionHistory(created_at=datetime(2023, 1, 1, 13, 0, 0))
        db.add_all([dh1, dh2])
        db.flush()  # obtain IDs

        # Three job runs linked to the latest definition‑history (dh2).
        jobs = [
            CadenceJobRun(
                definition_history_id=dh2.id,
                job_name="ingest",
                status="completed",
                started_at=datetime(2023, 1, 1, 13, 5, 0),
                finished_at=datetime(2023, 1, 1, 13, 10, 0),
                rows_affected=100,
            ),
            CadenceJobRun(
                definition_history_id=dh2.id,
                job_name="score",
                status="running",
                started_at=datetime(2023, 1, 1, 13, 11, 0),
                finished_at=None,
                rows_affected=None,
            ),
            CadenceJobRun(
                definition_history_id=dh2.id,
                job_name="export",
                status="pending",
                started_at=None,
                finished_at=None,
                rows_affected=None,
            ),
        ]
        db.add_all(jobs)
        db.commit()

    # Build FastAPI app for testing.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get(
        "/api/mcp/definition-history/pipeline-status"
    )
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict), "Response is not a dict"
    assert "jobs" in data, "Missing jobs key"
    assert len(data["jobs"]) == 3, f"Expected 3 jobs, got {len(data['jobs'])}"
    statuses = {job["status"] for job in data["jobs"]}
    assert "completed" in statuses, "Known status 'completed' not found"
    print("PASS")