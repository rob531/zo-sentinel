from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from app.db import get_session
from sqlalchemy import text

router = APIRouter(prefix="/api", tags=["pipeline"])


class PipelineStatusEntry(BaseModel):
    job: str
    status: str
    count: int


class PipelineStatusResponse(BaseModel):
    statuses: List[PipelineStatusEntry]


def get_pipeline_status_logic(session) -> PipelineStatusResponse:
    query = text("""
        SELECT job, status, COUNT(*) as count
        FROM cadence_job_runs
        GROUP BY job, status
        ORDER BY job, status
    """)
    result = session.execute(query).fetchall()
    statuses = [
        PipelineStatusEntry(job=row.job, status=row.status, count=row.count)
        for row in result
    ]
    return PipelineStatusResponse(statuses=statuses)


@router.get("/pipeline/status", response_model=PipelineStatusResponse)
def get_pipeline_status(session=Depends(get_session)):
    return get_pipeline_status_logic(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE cadence_job_runs (
                id INTEGER PRIMARY KEY,
                job TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMP,
                finished_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            INSERT INTO cadence_job_runs (job, status, started_at, finished_at) VALUES
            ('build', 'success', '2024-01-01 10:00:00', '2024-01-01 10:01:00'),
            ('build', 'success', '2024-01-01 11:00:00', '2024-01-01 11:01:00'),
            ('test', 'failed', '2024-01-01 10:00:00', '2024-01-01 10:01:00'),
            ('deploy', 'pending', '2024-01-01 10:00:00', NULL)
        """))

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/pipeline/status")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["statuses"]) == 3, f"Expected 3 statuses, got {len(data['statuses'])}"

    jobs = [s["job"] for s in data["statuses"]]
    assert "build" in jobs, "Expected 'build' job in results"

    print("PASS")