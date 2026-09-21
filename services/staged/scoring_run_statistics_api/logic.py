import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import Base, CadenceJobRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scoring"])


class ScoringRunStatisticsResponse(BaseModel):
    summary: dict
    per_job_stats: list[dict]


@router.get("/scoring/runs/statistics", response_model=ScoringRunStatisticsResponse)
def get_scoring_run_statistics(db: Session = Depends(get_session)):
    runs = db.query(CadenceJobRun).order_by(CadenceJobRun.started_at.desc()).all()
    
    if not runs:
        return ScoringRunStatisticsResponse(
            summary={"total_runs": 0, "pass_count": 0, "fail_count": 0, "error_count": 0},
            per_job_stats=[]
        )
    
    job_groups = {}
    for run in runs:
        if run.job not in job_groups:
            job_groups[run.job] = []
        job_groups[run.job].append(run)
    
    per_job_stats = []
    for job_name, job_runs in job_groups.items():
        total_runs = len(job_runs)
        pass_count = sum(1 for r in job_runs if r.status == "pass")
        fail_count = sum(1 for r in job_runs if r.status == "fail")
        error_count = sum(1 for r in job_runs if r.status == "error")
        
        durations = []
        for r in job_runs:
            if r.started_at and r.finished_at:
                durations.append((r.finished_at - r.started_at).total_seconds())
        
        avg_duration = sum(durations) / len(durations) if durations else None
        
        last_run = job_runs[0]
        
        per_job_stats.append({
            "job": job_name,
            "total_runs": total_runs,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "error_count": error_count,
            "avg_duration_seconds": avg_duration,
            "last_run_started": last_run.started_at,
            "last_run_finished": last_run.finished_at,
            "last_run_status": last_run.status,
            "rows_affected_last": last_run.rows_affected
        })
    
    total_runs_all = sum(s["total_runs"] for s in per_job_stats)
    pass_count_all = sum(s["pass_count"] for s in per_job_stats)
    fail_count_all = sum(s["fail_count"] for s in per_job_stats)
    error_count_all = sum(s["error_count"] for s in per_job_stats)
    
    return ScoringRunStatisticsResponse(
        summary={
            "total_runs": total_runs_all,
            "pass_count": pass_count_all,
            "fail_count": fail_count_all,
            "error_count": error_count_all
        },
        per_job_stats=per_job_stats
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    now = datetime.utcnow()
    test_runs = [
        CadenceJobRun(
            job="test_job",
            status="pass",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=50),
            rows_affected=100,
            detail="OK"
        ),
        CadenceJobRun(
            job="test_job",
            status="pass",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=45),
            rows_affected=150,
            detail="OK"
        ),
        CadenceJobRun(
            job="test_job",
            status="fail",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=55),
            rows_affected=0,
            detail="Failed"
        ),
        CadenceJobRun(
            job="test_job",
            status="running",
            started_at=now - timedelta(minutes=5),
            finished_at=None,
            rows_affected=0,
            detail="Running"
        ),
    ]
    
    db.add_all(test_runs)
    db.commit()
    
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: db
    app.include_router(router)
    
    client = TestClient(app)
    response = client.get("/api/scoring/runs/statistics")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["summary"]["total_runs"] == 4
    assert data["summary"]["pass_count"] == 2
    assert data["summary"]["fail_count"] == 1
    
    print("PASS")
    
    db.close()