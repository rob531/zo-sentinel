from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["escalated_servers"])


class EscalatedServerResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    escalated_to: str
    scored_at: str

    class Config:
        from_attributes = True


@router.get("/servers/escalated", response_model=List[EscalatedServerResponse])
def get_escalated_servers(session: Session = Depends(get_session)) -> List[EscalatedServerResponse]:
    query = text("""
        SELECT server_id, axis_name, label, escalated_to, scored_at
        FROM mcp_llm_axis_scores
        WHERE escalated = true
        ORDER BY scored_at DESC
    """)
    result = session.execute(query)
    rows = result.fetchall()
    return [
        EscalatedServerResponse(
            server_id=row[0],
            axis_name=row[1],
            label=row[2],
            escalated_to=row[3],
            scored_at=str(row[4])
        )
        for row in rows
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    def seed_data(session: Session):
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                adapter_sha256 TEXT,
                decision_rule_version TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                scored_at TIMESTAMP,
                escalated BOOLEAN,
                escalated_to TEXT
            )
        """))
        session.execute(text("DELETE FROM mcp_llm_axis_scores"))
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores VALUES
            (1, 'srv-001', 'criticality', 'critical', 0, 'v1', 'abc', 'v1', 0.8, 0.6, 0.2, '[0.8]', '2024-01-15 10:00:00', 1, 'senior_reviewer'),
            (2, 'srv-002', 'freshness', 'stale', 1, 'v1', 'def', 'v1', 0.3, 0.1, 0.9, '[0.3]', '2024-01-15 11:00:00', 0, NULL),
            (3, 'srv-003', 'criticality', 'critical', 0, 'v1', 'ghi', 'v1', 0.9, 0.7, 0.1, '[0.9]', '2024-01-15 12:00:00', 1, 'senior_reviewer'),
            (4, 'srv-004', 'precision', 'medium', 2, 'v1', 'jkl', 'v1', 0.5, 0.3, 0.5, '[0.5]', '2024-01-15 13:00:00', 0, NULL)
        """))
        session.commit()

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine)
    test_session = TestingSessionLocal()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        seed_data(test_session)
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/servers/escalated")
    assert response.status_code == 200
    data = response.json()
    assert len([s for s in data if s.get("escalated_to") is not None]) == 2
    print("PASS")