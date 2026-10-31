from __future__ import annotations

from datetime import datetime
from inspect import isawaitable
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_escalated_axis_distribution

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


class EscalatedTierCount(BaseModel):
    tier: str
    count: int


class AxisEscalationDistribution(BaseModel):
    axis_name: str
    label: str
    scored: int
    escalated: int
    escalation_ratio: float
    escalated_to: List[EscalatedTierCount]


class EscalatedAxisDistributionResponse(BaseModel):
    days: int
    generated_at: datetime
    axes: List[AxisEscalationDistribution]


@router.get(
    "/escalated-axis-distribution",
    response_model=EscalatedAxisDistributionResponse,
)
async def escalated_axis_distribution(
    days: int = Query(default=30, ge=1, le=3650),
    axis_name: Optional[str] = Query(default=None),
    session=Depends(get_session),
):
    result = get_escalated_axis_distribution(session, days, axis_name)
    if isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    table_name = McpLlmAxisScore.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    adapter_sha256 VARCHAR,
                    axis_name VARCHAR NOT NULL,
                    decision_rule_version VARCHAR,
                    escalated BOOLEAN,
                    escalated_to VARCHAR,
                    id VARCHAR NOT NULL,
                    label VARCHAR,
                    label_index INTEGER,
                    model_version VARCHAR NOT NULL,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    p_top FLOAT,
                    probs TEXT,
                    scored_at DATETIME,
                    server_id VARCHAR NOT NULL
                )
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    testing_app = FastAPI()
    testing_app.include_router(router)
    testing_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with SessionLocal() as session:
        session.execute(
            text(
                f"""
                INSERT INTO {table_name} (
                    adapter_sha256, axis_name, decision_rule_version,
                    escalated, escalated_to, id, label, label_index,
                    model_version, p_critical, p_danger, p_top, probs,
                    scored_at, server_id
                ) VALUES (
                    :adapter_sha256, :axis_name, :decision_rule_version,
                    :escalated, :escalated_to, :id, :label, :label_index,
                    :model_version, :p_critical, :p_danger, :p_top, :probs,
                    :scored_at, :server_id
                )
                """
            ),
            [
                {
                    "adapter_sha256": "adapter-a",
                    "axis_name": "auth_strength",
                    "decision_rule_version": "v1",
                    "escalated": True,
                    "escalated_to": "security",
                    "id": "00000000-0000-0000-0000-000000000001",
                    "label": "Authentication Strength",
                    "label_index": 1,
                    "model_version": "student-v1",
                    "p_critical": 0.1,
                    "p_danger": 0.2,
                    "p_top": 0.3,
                    "probs": "{}",
                    "scored_at": now,
                    "server_id": "server-1",
                },
                {
                    "adapter_sha256": "adapter-b",
                    "axis_name": "auth_strength",
                    "decision_rule_version": "v1",
                    "escalated": False,
                    "escalated_to": None,
                    "id": "00000000-0000-0000-0000-000000000002",
                    "label": "Authentication Strength",
                    "label_index": 1,
                    "model_version": "student-v1",
                    "p_critical": 0.0,
                    "p_danger": 0.1,
                    "p_top": 0.2,
                    "probs": "{}",
                    "scored_at": now,
                    "server_id": "server-2",
                },
                {
                    "adapter_sha256": "adapter-c",
                    "axis_name": "data_sensitivity",
                    "decision_rule_version": "v1",
                    "escalated": False,
                    "escalated_to": None,
                    "id": "00000000-0000-0000-0000-000000000003",
                    "label": "Data Sensitivity",
                    "label_index": 3,
                    "model_version": "student-v1",
                    "p_critical": 0.0,
                    "p_danger": 0.0,
                    "p_top": 0.1,
                    "probs": "{}",
                    "scored_at": now,
                    "server_id": "server-3",
                },
            ],
        )
        session.commit()

    with TestClient(testing_app) as client:
        response = client.get(
            "/api/scoring/escalated-axis-distribution",
            params={"days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        axes = payload["axes"]
        assert len(axes) == len({row["axis_name"] for row in axes})
        assert len(axes) == 2
        auth_axis = next(axis for axis in axes if axis["axis_name"] == "auth_strength")
        assert auth_axis["escalated"] == 1
        assert 0 <= auth_axis["escalation_ratio"] <= 1

    print("PASS")