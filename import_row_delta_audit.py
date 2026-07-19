from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScores,
    McpScoreDisputes,
    Org,
    User,
)
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class ImportRowDeltaAudit(BaseModel):
    batch_id: str
    table_name: str
    rows_before: int
    rows_after: int
    rows_added: int
    rows_updated: int
    rows_deleted: int
    net_change: int
    anomaly_flag: bool
    generated_at: datetime

def get_mesh_table_counts(session: Session, table_name: str) -> dict:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT COUNT(*) FROM {table_name}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to query mesh tables")
    return response.json()[0]

def calculate_anomaly_flag(net_change: int) -> bool:
    return abs(net_change) > 1000  # Threshold for anomaly detection

@router.get("/row-delta-audit", response_model=List[ImportRowDeltaAudit])
async def get_import_row_delta_audit(
    table_name: Optional[str] = None,
    since_minutes: int = 1440,
    session: Session = Depends(get_session)
):
    cutoff_time = datetime.utcnow() - timedelta(minutes=since_minutes)
    tables = {
        "mcp_server_registry": McpServerRegistry,
        "mcp_llm_axis_scores": McpLlmAxisScores,
        "mcp_score_disputes": McpScoreDisputes,
        "orgs": Org,
        "users": User,
        "mcp_signal_scores": None,
        "mesh_memory": None,
    }

    if table_name and table_name not in tables:
        raise HTTPException(status_code=400, detail="Invalid table name")

    results = []
    for name, model in tables.items():
        if table_name and name != table_name:
            continue

        if model is None:
            # Mesh tables
            try:
                before = get_mesh_table_counts(session, name)
                after = get_mesh_table_counts(session, name)
                rows_before = before["count"]
                rows_after = after["count"]
            except Exception as e:
                continue
        else:
            # App tables
            before = session.query(func.count()).select_from(model).filter(
                model.created_at < cutoff_time
            ).scalar() or 0
            after = session.query(func.count()).select_from(model).filter(
                model.created_at >= cutoff_time
            ).scalar() or 0
            rows_before = before
            rows_after = after

        rows_added = max(0, rows_after - rows_before)
        rows_deleted = max(0, rows_before - rows_after)
        rows_updated = 0  # Not tracked in this implementation
        net_change = rows_added - rows_deleted
        anomaly_flag = calculate_anomaly_flag(net_change)

        results.append(
            ImportRowDeltaAudit(
                batch_id=f"{name}-{cutoff_time.isoformat()}",
                table_name=name,
                rows_before=rows_before,
                rows_after=rows_after,
                rows_added=rows_added,
                rows_updated=rows_updated,
                rows_deleted=rows_deleted,
                net_change=net_change,
                anomaly_flag=anomaly_flag,
                generated_at=datetime.utcnow(),
            )
        )

    return results

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test tables
    TestSession().execute("CREATE TABLE mcp_server_registry (id INTEGER PRIMARY KEY, created_at TIMESTAMP)")
    TestSession().execute("CREATE TABLE mcp_llm_axis_scores (id INTEGER PRIMARY KEY, created_at TIMESTAMP)")
    TestSession().execute("CREATE TABLE mcp_score_disputes (id INTEGER PRIMARY KEY, created_at TIMESTAMP)")
    TestSession().execute("CREATE TABLE orgs (id INTEGER PRIMARY KEY, created_at TIMESTAMP)")
    TestSession().execute("CREATE TABLE users (id INTEGER PRIMARY KEY, created_at TIMESTAMP)")

    # Add test data
    session = TestSession()
    session.execute("INSERT INTO mcp_server_registry (created_at) VALUES (datetime('now', '-1500 minutes'))")
    session.execute("INSERT INTO mcp_server_registry (created_at) VALUES (datetime('now', '-1400 minutes'))")
    session.execute("INSERT INTO mcp_llm_axis_scores (created_at) VALUES (datetime('now', '-1500 minutes'))")
    session.execute("INSERT INTO mcp_llm_axis_scores (created_at) VALUES (datetime('now', '-1400 minutes'))")
    session.commit()

    client = TestClient(app)

    response = client.get("/row-delta-audit?since_minutes=1440")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for entry in data:
        assert "rows_before" in entry
        assert "rows_after" in entry
        assert "net_change" in entry
        assert isinstance(entry["rows_before"], int)
        assert isinstance(entry["rows_after"], int)
        assert isinstance(entry["net_change"], int)
        assert isinstance(entry["anomaly_flag"], bool)

    print("PASS")