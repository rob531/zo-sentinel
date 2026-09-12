from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List
import sys

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


app = FastAPI()


class AxisScore(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    scored_at: str


class AxisScoresResponse(BaseModel):
    server_id: str
    axes: List[AxisScore]


@app.get("/api/servers/{server_id}/axis-scores", response_model=AxisScoresResponse)
def get_axis_scores(server_id: str, session: Session = Depends(get_session)):
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    axes = [
        AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=row.escalated,
            scored_at=row.scored_at.isoformat() if row.scored_at else None,
        )
        for row in rows
    ]
    return AxisScoresResponse(server_id=server_id, axes=axes)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                server_name TEXT,
                org_id TEXT,
                created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER,
                scored_at TIMESTAMP
            )
        """))

    session = TestingSessionLocal()

    session.add(McpServerRegistry(server_id="srv-001", server_name="Server One", org_id="org-1"))
    session.add(McpServerRegistry(server_id="srv-002", server_name="Server Two", org_id="org-1"))

    from datetime import datetime
    base_time = datetime(2024, 1, 15, 10, 0, 0)

    session.add(McpLlmAxisScore(
        server_id="srv-001", axis_name="risk", label="High", label_index=2,
        p_top=0.75, p_critical=0.15, p_danger=0.10, escalated=1, scored_at=base_time
    ))
    session.add(McpLlmAxisScore(
        server_id="srv-001", axis_name="compliance", label="Medium", label_index=1,
        p_top=0.30, p_critical=0.40, p_danger=0.30, escalated=0, scored_at=base_time
    ))
    session.add(McpLlmAxisScore(
        server_id="srv-001", axis_name="stability", label="Low", label_index=0,
        p_top=0.10, p_critical=0.20, p_danger=0.70, escalated=0, scored_at=base_time
    ))
    session.add(McpLlmAxisScore(
        server_id="srv-002", axis_name="risk", label="Critical", label_index=3,
        p_top=0.95, p_critical=0.03, p_danger=0.02, escalated=1, scored_at=base_time
    ))
    session.add(McpLlmAxisScore(
        server_id="srv-002", axis_name="performance", label="High", label_index=2,
        p_top=0.60, p_critical=0.30, p_danger=0.10, escalated=1, scored_at=base_time
    ))
    session.add(McpLlmAxisScore(
        server_id="srv-002", axis_name="availability", label="Medium", label_index=1,
        p_top=0.25, p_critical=0.45, p_danger=0.30, escalated=0, scored_at=base_time
    ))
    session.commit()
    session.close()

    client = TestClient(app)

    response = client.get("/api/servers/srv-001/axis-scores")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "server_id" in data
    assert "axes" in data
    assert len(data["axes"]) == 3, f"Expected 3 axes, got {len(data['axes'])}"

    p_top_values = [ax["p_top"] for ax in data["axes"]]
    assert 0.75 in p_top_values, f"Expected p_top=0.75 in {p_top_values}"

    print("PASS")
    sys.exit(0)