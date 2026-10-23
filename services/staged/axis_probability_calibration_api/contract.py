from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from math import floor
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class CalibrationBucket(BaseModel):
    bucket_lo: float
    bucket_hi: float
    count: int = Field(ge=0)
    mean_p_top: float
    observed_high_risk_rate: float


class AxisCalibrationResponse(BaseModel):
    axis_name: str
    buckets: list[CalibrationBucket]
    total_rows: int = Field(ge=0)
    generated_at: datetime


def calculate_axis_calibration(
    session: Session,
    axis_name: str,
    bucket_count: int,
) -> dict[str, Any]:
    rows = session.execute(
        select(McpLlmAxisScore.p_top, McpServerRegistry.risk_tier)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
    ).all()

    groups: dict[int, list[float]] = {}
    high_risk_counts: dict[int, int] = {}

    for p_top, risk_tier in rows:
        if p_top is None:
            continue

        probability = min(max(float(p_top), 0.0), 1.0)
        bucket_index = floor(probability * bucket_count)
        if probability == 1.0:
            bucket_index = bucket_count - 1
        bucket_index = min(max(bucket_index, 0), bucket_count - 1)

        groups.setdefault(bucket_index, []).append(probability)
        if str(risk_tier or "").upper() in {"CRITICAL", "HIGH"}:
            high_risk_counts[bucket_index] = high_risk_counts.get(bucket_index, 0) + 1

    buckets = [
        CalibrationBucket(
            bucket_lo=bucket_index / bucket_count,
            bucket_hi=(bucket_index + 1) / bucket_count,
            count=len(probabilities),
            mean_p_top=sum(probabilities) / len(probabilities),
            observed_high_risk_rate=high_risk_counts.get(bucket_index, 0)
            / len(probabilities),
        )
        for bucket_index, probabilities in sorted(groups.items())
    ]

    return AxisCalibrationResponse(
        axis_name=axis_name,
        buckets=buckets,
        total_rows=sum(len(probabilities) for probabilities in groups.values()),
        generated_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")


router = APIRouter(prefix="/api")


@router.get("/axes/calibration", response_model=AxisCalibrationResponse)
def get_axis_calibration(
    axis_name: str = Query(..., min_length=1),
    bucket: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return calculate_axis_calibration(session, axis_name, bucket)


app = FastAPI(title="Axis Probability Calibration API")
app.include_router(router)


def build_test_app() -> FastAPI:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=test_engine,
        class_=Session,
        expire_on_commit=False,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT
                )
                """
            )
        )

        registry_rows = [
            ("server-critical", "CRITICAL"),
            ("server-trusted-1", "TRUSTED"),
            ("server-trusted-2", "TRUSTED"),
        ]
        for server_id, risk_tier in registry_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (server_id, risk_tier)
                    VALUES (:server_id, :risk_tier)
                    """
                ),
                {"server_id": server_id, "risk_tier": risk_tier},
            )

        score_rows = [
            (1, "server-critical", "overall_risk", 0.9, 0.05, 0.05),
            (2, "server-trusted-1", "overall_risk", 0.5, 0.20, 0.30),
            (3, "server-trusted-2", "overall_risk", 0.1, 0.40, 0.50),
        ]
        for row_id, server_id, axis_name, p_top, p_critical, p_danger in score_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (
                        id, server_id, axis_name, p_top, p_critical, p_danger
                    )
                    VALUES (
                        :id, :server_id, :axis_name, :p_top, :p_critical, :p_danger
                    )
                    """
                ),
                {
                    "id": row_id,
                    "server_id": server_id,
                    "axis_name": axis_name,
                    "p_top": p_top,
                    "p_critical": p_critical,
                    "p_danger": p_danger,
                },
            )

    def override_get_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app = FastAPI(title="Axis Probability Calibration API Test")
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    return test_app


def run_contract() -> None:
    test_app = build_test_app()

    with TestClient(test_app) as client:
        response = client.get(
            "/api/axes/calibration",
            params={"axis_name": "overall_risk", "bucket": 10},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["axis_name"] == "overall_risk"
        assert payload["total_rows"] == 3
        assert len(payload["buckets"]) == 3

        top_bucket = next(
            bucket
            for bucket in payload["buckets"]
            if bucket["bucket_lo"] <= 0.9 <= bucket["bucket_hi"]
        )
        assert top_bucket["observed_high_risk_rate"] == 1.0

    print("PASS")


run = run_contract
contract_check = run_contract
main = run_contract


if __name__ == "__main__":
    main()