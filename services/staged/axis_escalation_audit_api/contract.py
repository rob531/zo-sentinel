from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["audit"])


class AxisEscalationCount(BaseModel):
    axis_name: str
    server_count: int


class EscalatedToDistribution(BaseModel):
    escalated_to: str
    count: int


class ModelVersionBreakdown(BaseModel):
    model_version: str
    count: int


class AxisEscalationResponse(BaseModel):
    axis_counts: List[AxisEscalationCount]
    escalated_to_distribution: List[EscalatedToDistribution]
    model_version_breakdown: List[ModelVersionBreakdown]


@router.get("/audit/axis-escalations", response_model=AxisEscalationResponse)
def get_axis_escalations(session: Session = Depends(get_session)) -> AxisEscalationResponse:
    axis_counts_query = text("""
        SELECT axis_name, COUNT(DISTINCT server_id) as server_count
        FROM mcp_llm_axis_scores
        WHERE escalated = true
        GROUP BY axis_name
        ORDER BY axis_name
    """)
    axis_counts_result = session.execute(axis_counts_query).fetchall()
    axis_counts = [
        AxisEscalationCount(axis_name=row.axis_name, server_count=row.server_count)
        for row in axis_counts_result
    ]

    escalated_to_query = text("""
        SELECT escalated_to, COUNT(*) as count
        FROM mcp_llm_axis_scores
        WHERE escalated = true AND escalated_to IS NOT NULL
        GROUP BY escalated_to
        ORDER BY escalated_to
    """)
    escalated_to_result = session.execute(escalated_to_query).fetchall()
    escalated_to_distribution = [
        EscalatedToDistribution(escalated_to=row.escalated_to, count=row.count)
        for row in escalated_to_result
    ]

    model_version_query = text("""
        SELECT model_version, COUNT(*) as count
        FROM mcp_llm_axis_scores
        WHERE escalated = true
        GROUP BY model_version
        ORDER BY model_version
    """)
    model_version_result = session.execute(model_version_query).fetchall()
    model_version_breakdown = [
        ModelVersionBreakdown(model_version=row.model_version, count=row.count)
        for row in model_version_result
    ]

    return AxisEscalationResponse(
        axis_counts=axis_counts,
        escalated_to_distribution=escalated_to_distribution,
        model_version_breakdown=model_version_breakdown
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    McpLlmAxisScore.__table__.create(in_memory_engine, checkfirst=True)

    TestingSessionLocal = sessionmaker(bind=in_memory_engine)
    test_session = TestingSessionLocal()

    test_session.execute(text("""
        INSERT INTO mcp_llm_axis_scores 
        (id, server_id, axis_name, model_version, escalated, escalated_to, scored_at, adapter_sha256)
        VALUES 
        (1, 'server-1', 'risk', 'v1.0', 1, 'security_team', datetime('now'), 'sha256hash1'),
        (2, 'server-2', 'risk', 'v1.0', 1, 'security_team', datetime('now'), 'sha256hash2'),
        (3, 'server-3', 'compliance', 'v1.0', 1, 'compliance_officer', datetime('now'), 'sha256hash3'),
        (4, 'server-4', 'availability', 'v2.0', 1, 'ops_team', datetime('now'), 'sha256hash4')
    """))
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/audit/axis-escalations")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "axis_counts" in data, "Missing axis_counts in response"
    assert "escalated_to_distribution" in data, "Missing escalated_to_distribution in response"
    assert "model_version_breakdown" in data, "Missing model_version_breakdown in response"

    axis_counts = {item["axis_name"]: item["server_count"] for item in data["axis_counts"]}
    assert axis_counts.get("risk") == 2, f"Expected risk=2, got {axis_counts.get('risk')}"
    assert axis_counts.get("compliance") == 1, f"Expected compliance=1, got {axis_counts.get('compliance')}"
    assert axis_counts.get("availability") == 1, f"Expected availability=1, got {axis_counts.get('availability')}"

    escalated_dist = {item["escalated_to"]: item["count"] for item in data["escalated_to_distribution"]}
    assert escalated_dist.get("security_team") == 2, f"Expected security_team=2, got {escalated_dist.get('security_team')}"
    assert escalated_dist.get("compliance_officer") == 1, f"Expected compliance_officer=1, got {escalated_dist.get('compliance_officer')}"
    assert escalated_dist.get("ops_team") == 1, f"Expected ops_team=1, got {escalated_dist.get('ops_team')}"

    print("PASS")