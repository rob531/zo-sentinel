from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")

MAX_SERVERS_PER_BUCKET = 25


class AxisProbabilityBucket(BaseModel):
    min: float
    max: float
    count: int
    servers: List[str]


class AxisProbabilityMatrixResponse(BaseModel):
    axis_name: Optional[str]
    bucket_size: int
    buckets: List[AxisProbabilityBucket]
    tier_mix: Dict[str, int]
    total: int


@router.get(
    "/axes/probability-matrix",
    response_model=AxisProbabilityMatrixResponse,
)
def get_axis_probability_matrix(
    axis_name: Optional[str] = Query(default=None),
    bucket_size: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> AxisProbabilityMatrixResponse:
    statement = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .order_by(McpLlmAxisScore.p_top, McpLlmAxisScore.server_id)
    )
    if axis_name is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis_name)

    rows = session.execute(statement).all()

    bucket_count = (100 + bucket_size - 1) // bucket_size
    bucket_width = bucket_size / 100.0
    bucket_rows: List[List[str]] = [[] for _ in range(bucket_count)]
    tier_mix: Dict[str, int] = {}
    total = 0

    for server_id, row_axis_name, p_top, risk_tier in rows:
        if p_top is None:
            continue

        value = max(0.0, min(1.0, float(p_top)))
        index = min(bucket_count - 1, int(value * 100) // bucket_size)
        server_key = str(server_id)
        bucket_rows[index].append(server_key)

        tier_key = str(risk_tier) if risk_tier not in (None, "") else "unknown"
        tier_mix[tier_key] = tier_mix.get(tier_key, 0) + 1
        total += 1

    buckets = []
    for index, servers in enumerate(bucket_rows):
        lower = round(index * bucket_width, 10)
        upper = round(min((index + 1) * bucket_width, 1.0), 10)
        buckets.append(
            AxisProbabilityBucket(
                min=lower,
                max=upper,
                count=len(servers),
                servers=servers[:MAX_SERVERS_PER_BUCKET],
            )
        )

    return AxisProbabilityMatrixResponse(
        axis_name=axis_name,
        bucket_size=bucket_size,
        buckets=buckets,
        tier_mix=dict(sorted(tier_mix.items())),
        total=total,
    )


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR,
                    verdict VARCHAR
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
                    label VARCHAR,
                    p_top FLOAT,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    probs TEXT,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, verdict)
                VALUES
                    ('server-1', 'Server One', 'high', 'block'),
                    ('server-2', 'Server Two', 'medium', 'review'),
                    ('server-3', 'Server Three', 'low', 'allow')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, p_top, p_critical, p_danger, probs, scored_at)
                VALUES
                    ('server-1', 'confidence', 'high', 0.05, 0.05, 0.10, '{}', '2024-01-01T00:00:00Z'),
                    ('server-1', 'severity', 'high', 0.25, 0.10, 0.15, '{}', '2024-01-01T00:00:00Z'),
                    ('server-2', 'confidence', 'medium', 0.15, 0.05, 0.10, '{}', '2024-01-01T00:00:00Z'),
                    ('server-2', 'severity', 'medium', 0.35, 0.10, 0.15, '{}', '2024-01-01T00:00:00Z'),
                    ('server-3', 'confidence', 'low', 0.95, 0.05, 0.10, '{}', '2024-01-01T00:00:00Z'),
                    ('server-3', 'severity', 'low', 0.45, 0.10, 0.15, '{}', '2024-01-01T00:00:00Z')
                """
            )
        )

    def override_get_session():
        with TestingSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/axes/probability-matrix",
            params={"axis_name": "confidence", "bucket_size": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["axis_name"] == "confidence"
        assert payload["bucket_size"] == 10
        assert payload["total"] == 3
        assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
        assert payload["tier_mix"] == {"high": 1, "low": 1, "medium": 1}
        assert all(len(bucket["servers"]) <= 25 for bucket in payload["buckets"])

    print("PASS")