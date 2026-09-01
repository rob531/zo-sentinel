from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter(prefix="/api", tags=["scoring_health"])


class JobHealth(BaseModel):
    job_name: str
    status_counts: Dict[str, int]
    avg_duration_sec: Optional[float]
    total_runs: int
    total_rows_affected: int


class HealthResponse(BaseModel):
    window_hours: int
    jobs: List[JobHealth]


def get_health_stats(session: Session, window_hours: int) -> HealthResponse:
    query = text("""
        SELECT 
            job_name,
            GROUP_CONCAT(status || ':' || cnt, ',') as status_counts,
            AVG(duration_sec) as avg_duration_sec,
            SUM(cnt) as total_runs,
            SUM(rows_affected) as total_rows_affected
        FROM (
            SELECT 
                job as job_name,
                status,
                COUNT(*) as cnt,
                SUM(rows_affected) as rows_affected,
                AVG(
                    (strftime('%J', finished_at) - strftime('%J', started_at)) * 86400
                ) as duration_sec
            FROM cadence_job_runs
            WHERE started_at >= datetime('now', :window_hours_expr)
            GROUP BY job, status
        )
        GROUP BY job_name
    """)
    
    window_expr = f"-{window_hours} hours"
    result = session.execute(query, {"window_hours_expr": window_expr})
    rows = result.fetchall()
    
    jobs = []
    for row in rows:
        job_name = row[0]
        status_counts_raw = row[1]
        avg_duration_sec = float(row[2]) if row[2] is not None else None
        total_runs = row[3]
        total_rows_affected = row[4]
        
        status_counts: Dict[str, int] = {}
        if status_counts_raw:
            for part in status_counts_raw.split(','):
                if ':' in part:
                    status, count = part.split(':')
                    status_counts[status] = int(count)
        
        jobs.append(JobHealth(
            job_name=job_name,
            status_counts=status_counts,
            avg_duration_sec=avg_duration_sec,
            total_runs=total_runs,
            total_rows_affected=total_rows_affected,
        ))
    
    return HealthResponse(window_hours=window_hours, jobs=jobs)


@router.get("/scoring/health", response_model=HealthResponse)
def health(
    window_hours: int = Query(default=24, ge=1),
    session: Session = Depends(get_session),
) -> HealthResponse:
    return get_health_stats(session, window_hours)


if __name__ == "__main__":
    import os
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE cadence_job_runs (
                id INTEGER PRIMARY KEY,
                job TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                finished_at TIMESTAMP NOT NULL,
                rows_affected INTEGER NOT NULL,
                detail TEXT
            )
        """))
        conn.commit()
    
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()
    
    now = datetime.utcnow()
    
    session.execute(text("""
        INSERT INTO cadence_job_runs (id, job, status, started_at, finished_at, rows_affected, detail)
        VALUES (:id, :job, :status, :started_at, :finished_at, :rows_affected, :detail)
    """), [
        {"id": 1, "job": "job_A", "status": "completed", "started_at": now.isoformat(), 
         "finished_at": (now).isoformat(), "rows_affected": 10, "detail": "done"},
        {"id": 2, "job": "job_B", "status": "failed", "started_at": (now).isoformat(), 
         "finished_at": (now).isoformat(), "rows_affected": 20, "detail": "error"},
        {"id": 3, "job": "job_A", "status": "completed", "started_at": (now).isoformat(), 
         "finished_at": (now).isoformat(), "rows_affected": 10, "detail": "success"},
        {"id": 4, "job": "job_B", "status": "completed", "started_at": (now).isoformat(), 
         "finished_at": (now).isoformat(), "rows_affected": 10, "detail": "success"},
    ])
    session.commit()
    
    from fastapi.testclient import TestClient
    
    def override_get_session():
        return session
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/scoring/health?window_hours=24")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["window_hours"] == 24
    assert len(data["jobs"]) == 2
    
    failing_jobs = [j for j in data["jobs"] if j.get("status_counts", {}).get("failed", 0) > 0]
    assert len(failing_jobs) == 1, f"Expected 1 job with failed status, got {len(failing_jobs)}"
    
    for job in data["jobs"]:
        assert "job_name" in job
        assert "status_counts" in job
        assert "avg_duration_sec" in job
        assert "total_runs" in job
        assert "total_rows_affected" in job
    
    print("PASS")