from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import CadenceJobRun
import json
import sqlite3

app = FastAPI()

class PipelineStatusResponse(BaseModel):
    completion: float
    last_run: str | None
    rows_affected: int

class StatusWrapper(BaseModel):
    status: PipelineStatusResponse

def read_bus_table(table_name: str) -> list[dict]:
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:8772/query",
        data=json.dumps({"sql": f"SELECT * FROM {table_name}"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read.read())
    except:
        return []

def compute_pipeline_status(session: Session) -> PipelineStatusResponse:
    result = session.execute(
        text("""
            SELECT 
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_runs,
                MAX(started_at) as last_run,
                COALESCE(SUM(rows_affected), 0) as total_rows
            FROM cadence_job_runs
            WHERE job = 'definition_history_pipeline'
        """)
    )
    row = result.fetchone()
    if row:
        total = row[0] or 1
        completed = row[1] or 0
        completion = (completed / total) * 100 if total > 0 else 0.0
        last_run = row[2].isoformat() if row[2] else None
        rows_affected = row[3] or 0
    else:
        completion = 0.0
        last_run = None
        rows_affected = 0
    return PipelineStatusResponse(
        completion=completion,
        last_run=last_run,
        rows_affected=rows_affected
    )

@app.get("/api/history/pipeline-status", response_model=StatusWrapper)
def get_pipeline_status(session: Session = Depends(get_session)):
    return StatusWrapper(status=compute_pipeline_status(session))

def run():
    print("PASS")

if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE cadence_job_runs (
                id INTEGER PRIMARY KEY,
                job TEXT,
                status TEXT,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                rows_affected INTEGER,
                detail TEXT
            )
        """))

    session = SessionLocal()
    session.add(CadenceJobRun(id=1, job="definition_history_pipeline", status="completed", started_at=None, finished_at=None, rows_affected=100, detail="test1"))
    session.add(CadenceJobRun(id=2, job="definition_history_pipeline", status="running", started_at=None, finished_at=None, rows_affected=50, detail="test2"))
    session.commit()
    session.close()

    client = TestClient(app)
    app.dependency_overrides[get_session] = override_get_session

    response = client.get("/api/history/pipeline-status")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "status" in data
    assert "completion" in data["status"]
    assert "last_run" in data["status"]
    assert "rows_affected" in data["status"]
    assert data["status"]["completion"] == 50.0
    assert data["status"]["rows_affected"] == 150

    run()