from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_p_top_distribution
except ImportError:  # Supports direct execution during the local self-test.
    from services.staged.axis_p_top_distribution_api.logic import get_p_top_distribution


class AxisPTopDistribution(BaseModel):
    axis_name: str
    count: int
    min_p_top: float
    max_p_top: float
    mean_p_top: float
    escalated_count: int
    histogram: List[int] = Field(min_length=10, max_length=10)


class PTopDistributionResponse(BaseModel):
    generated_at: datetime
    buckets: List[float] = Field(min_length=11, max_length=11)
    axes: List[AxisPTopDistribution]


router = APIRouter(prefix="/api", tags=["axis-p-top-distribution"])


@router.get(
    "/axes/p_top-distribution",
    response_model=PTopDistributionResponse,
    summary="Get the distribution of p_top scores for each real risk axis",
)
def p_top_distribution(
    session: Session = Depends(get_session),
) -> PTopDistributionResponse:
    return get_p_top_distribution(session)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL NOT NULL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated BOOLEAN NOT NULL DEFAULT FALSE,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    scored_at TEXT
                )
                """
            )
        )

        axes = (
            "overall_risk",
            "auth_strength",
            "capability_breadth",
        )
        values = (0.05, 0.5, 0.95)

        for server_number in range(3):
            for axis_name in axes:
                for value_number, p_top in enumerate(values):
                    connection.execute(
                        text(
                            """
                            INSERT INTO mcp_llm_axis_scores (
                                server_id,
                                axis_name,
                                label,
                                label_index,
                                probs,
                                p_top,
                                p_critical,
                                p_danger,
                                escalated,
                                escalated_to,
                                decision_rule_version,
                                model_version,
                                scored_at
                            ) VALUES (
                                :server_id,
                                :axis_name,
                                :label,
                                :label_index,
                                :probs,
                                :p_top,
                                :p_critical,
                                :p_danger,
                                :escalated,
                                :escalated_to,
                                :decision_rule_version,
                                :model_version,
                                :scored_at
                            )
                            """
                        ),
                        {
                            "server_id": f"server-{server_number}",
                            "axis_name": axis_name,
                            "label": f"label-{value_number}",
                            "label_index": value_number,
                            "probs": "{}",
                            "p_top": p_top,
                            "p_critical": p_top,
                            "p_danger": p_top,
                            "escalated": value_number == 2,
                            "escalated_to": None,
                            "decision_rule_version": "test",
                            "model_version": "test",
                            "scored_at": "2025-01-01T00:00:00Z",
                        },
                    )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/p_top-distribution")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["axes"]) == 3, payload
        assert payload["buckets"] == [
            0.0,
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.7,
            0.8,
            0.9,
            1.0,
        ], payload
        for axis in payload["axes"]:
            assert sum(axis["histogram"]) == axis["count"], axis
            assert axis["count"] == 9, axis
            assert axis["escalated_count"] == 3, axis

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()