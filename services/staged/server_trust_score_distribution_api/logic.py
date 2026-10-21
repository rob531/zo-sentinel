from __future__ import annotations

from math import ceil
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TrustScoreBucket(BaseModel):
    label: str
    lower: float
    upper: float
    count: int


class TrustScoreDistributionResponse(BaseModel):
    bucket_size: int
    total: int
    missing: int
    buckets: list[TrustScoreBucket]


def calculate_trust_score_distribution(
    session: Session,
    bucket_size: int = 10,
) -> dict[str, Any]:
    bucket_size = max(5, min(25, int(bucket_size)))
    bucket_count = ceil(100 / bucket_size)

    rows = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.trust_score,
    ).all()

    buckets = [
        {
            "label": f"{index * bucket_size}-{min((index + 1) * bucket_size, 100)}",
            "lower": float(index * bucket_size),
            "upper": float(min((index + 1) * bucket_size, 100)),
            "count": 0,
        }
        for index in range(bucket_count)
    ]

    missing = 0
    for _server_id, trust_score in rows:
        if trust_score is None:
            missing += 1
            continue

        score = float(trust_score)
        if score <= 0:
            bucket_index = 0
        elif score >= 100:
            bucket_index = bucket_count - 1
        else:
            bucket_index = min(int(score // bucket_size), bucket_count - 1)
        buckets[bucket_index]["count"] += 1

    return {
        "bucket_size": bucket_size,
        "total": len(rows),
        "missing": missing,
        "buckets": buckets,
    }


def get_trust_score_distribution(
    bucket_size: int = 10,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return calculate_trust_score_distribution(session=session, bucket_size=bucket_size)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = Session(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    trust_score FLOAT,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    registry_source VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, trust_score, risk_tier, verdict, registry_source, last_assessed)
                VALUES
                    ('server-1', 5.0, 'low', 'approved', 'test', CURRENT_TIMESTAMP),
                    ('server-2', 42.0, 'medium', 'conditional', 'test', CURRENT_TIMESTAMP),
                    ('server-3', 77.5, 'high', 'restricted', 'test', CURRENT_TIMESTAMP),
                    ('server-4', NULL, 'unknown', 'unknown', 'test', CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        with session_factory as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get(
        "/api/registry/trust-score-distribution",
        response_model=TrustScoreDistributionResponse,
    )
    def distribution_endpoint(
        bucket_size: int = 10,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return calculate_trust_score_distribution(
            session=session,
            bucket_size=bucket_size,
        )

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get("/api/registry/trust-score-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["missing"] == 1
        matching = [
            bucket
            for bucket in payload["buckets"]
            if bucket["lower"] <= 42.0 < bucket["upper"]
        ]
        assert len(matching) == 1
        assert matching[0]["count"] == 1

    print("PASS")


if __name__ == "__main__":
    run_self_test()