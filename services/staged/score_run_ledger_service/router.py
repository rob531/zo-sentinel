# services/staged/score_run_ledger_service/router.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["scoring"])


class LedgerEntry(BaseModel):
    date: str
    run_count: int
    avg_rows: float
    success_rate: float


class LedgerResponse(BaseModel):
    days: int
    entries: List[LedgerEntry]


def get_score_run_ledger_data(days: int, session: Session) -> LedgerResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    query = text("""
        SELECT 
            DATE(started_at) as run_date,
            COUNT(*) as run_count,
            COALESCE(AVG(rows_affected), 0) as avg_rows_affected,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)::float / COUNT(*) as success_rate
        FROM cadence_job_runs
        WHERE job LIKE 'score_run%'
          AND started_at >= :cutoff
        GROUP BY DATE(started_at)
        ORDER BY run_date DESC
    """)
    
    result = session.execute(query, {"cutoff": cutoff})
    rows = result.fetchall()
    
    entries = [
        LedgerEntry(
            date=str(row[0]),
            run_count=row[1],
            avg_rows=round(row[2], 2),
            success_rate=round(row[3], 4) if row[3] is not None else 0.0
        )
        for row in rows
    ]
    
    return LedgerResponse(days=days, entries=entries)


@router.get("/scoring/ledger", response_model=LedgerResponse)
def get_ledger(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session)
) -> LedgerResponse:
    return get_score_run_ledger_data(days, session)


if __name__ == "__main__":
    import os
    import sys
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    service_dir = os.path.dirname(current_dir)
    root_dir = os.path.dirname(service_dir)
    sys.path.insert(0, root_dir)
    
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    logic_module_name = "score_run_ledger_service.logic"
    import importlib
    logic_spec = importlib.util.find_spec(logic_module_name, package="score_run_ledger_service")
    if logic_spec and logic_spec.origin:
        from .logic import get_score_run_ledger_data
    else:
        import sqlite3
        sys.path.insert(0, current_dir)
        import logic as logic_mod
        get_score_run_ledger_data = logic_mod.get_score_run_ledger_data
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    
    conn = test_engine.connect()
    conn.execute(text("""
        INSERT INTO cadence_job_runs (job, status, started_at, finished_at, rows_affected, detail)
        VALUES 
            ('score_run_daily', 'success', datetime('now', '-1 day'), datetime('now', '-1 day', '+1 hour'), 150, 'daily'),
            ('score_run_weekly', 'success', datetime('now', '-1 day'), datetime('now', '-1 day', '+2 hours'), 200, 'weekly'),
            ('score_run_nightly', 'failure', datetime('now', '-3 days'), datetime('now', '-3 days', '+30 min'), 0, 'nightly failed')
    """))
    conn.commit()
    conn.close()
    
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    response = client.get("/api/scoring/ledger?days=7")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert "entries" in data
    assert len(data["entries"]) > 0, "Expected at least one entry"
    
    print("PASS")