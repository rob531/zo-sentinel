from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Literal
from datetime import datetime
import httpx

from app.db import get_session
from app.models import McpLlmAxisScore


router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class MaintainerTrustResponse(BaseModel):
    server_id: str
    axis: Literal["maintainer_trust"]
    p_top: float
    p_critical: float
    p_danger: float
    label: str
    scored_at: datetime
    decision_rule_version: str
    model_version: str
    adapter_sha256: str


def read_maintainer_trust_axis(server_id: str) -> dict:
    with httpx.Client(base_url="http://127.0.0.1:8772") as client:
        response = client.post(
            "/query",
            json={
                "sql": "SELECT server_id, axis_name, p_top, p_critical, p_danger, label, scored_at, decision_rule_version, model_version, adapter_sha256 FROM mcp_llm_axis_scores WHERE axis_name = 'maintainer_trust' AND server_id = $1",
                "params": [server_id],
            },
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            raise ValueError(f"No maintainer_trust score found for server_id={server_id}")
        return results[0]


@router.get("/maintainer_trust/{server_id}", response_model=MaintainerTrustResponse)
async def get_maintainer_trust_score(
    server_id: str,
    session=Depends(get_session),
) -> MaintainerTrustResponse:
    row = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.axis_name == "maintainer_trust",
        McpLlmAxisScore.server_id == server_id,
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail=f"No maintainer_trust score for {server_id}")

    return MaintainerTrustResponse(
        server_id=row.server_id,
        axis="maintainer_trust",
        p_top=row.p_top,
        p_critical=row.p_critical,
        p_danger=row.p_danger,
        label=row.label,
        scored_at=row.scored_at,
        decision_rule_version=row.decision_rule_version,
        model_version=row.model_version,
        adapter_sha256=row.adapter_sha256,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                scored_at TIMESTAMP,
                decision_rule_version TEXT,
                model_version TEXT,
                adapter_sha256 TEXT,
                escalated INTEGER,
                escalated_to TEXT
            )
        """))

        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, axis_name, p_top, p_critical, p_danger, label, label_index, scored_at, decision_rule_version, model_version, adapter_sha256, escalated)
            VALUES 
            ('srv_high_trust', 'maintainer_trust', 0.85, 0.05, 0.10, 'high_trust', 0, '2025-01-15T10:00:00', 'v1.0', 'gpt-4', 'abc123', 0),
            ('srv_low_trust', 'maintainer_trust', 0.20, 0.30, 0.50, 'low_trust', 2, '2025-01-15T10:00:00', 'v1.0', 'gpt-4', 'def456', 0)
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

    response = client.get("/api/scoring/maintainer_trust/srv_high_trust")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "p_top" in data, "Missing p_top in response"
    assert "label" in data, "Missing label in response"
    assert data["server_id"] == "srv_high_trust"
    assert data["axis"] == "maintainer_trust"
    assert data["p_top"] == 0.85

    response2 = client.get("/api/scoring/maintainer_trust/srv_low_trust")
    assert response2.status_code == 200

    print("PASS")