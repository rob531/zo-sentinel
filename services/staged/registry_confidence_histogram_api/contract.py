from collections import Counter
from decimal import Decimal, ROUND_FLOOR

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.models import McpServerRegistry


class ConfidenceBucket(BaseModel):
    lo: float
    hi: float
    count: int


class VerdictCount(BaseModel):
    verdict: str
    count: int


class ConfidenceHistogramResponse(BaseModel):
    bins: int
    buckets: list[ConfidenceBucket]
    unknown_count: int
    total: int
    verdicts: list[VerdictCount]


router = APIRouter(prefix="/api")


@router.get(
    "/registry/confidence-histogram",
    response_model=ConfidenceHistogramResponse,
)
def get_registry_confidence_histogram(
    bins: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ConfidenceHistogramResponse:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.confidence,
            McpServerRegistry.verdict,
        )
    ).all()

    counts = [0] * bins
    unknown_count = 0
    verdict_counts: Counter[str] = Counter()

    for row in rows:
        if row.verdict is not None:
            verdict_counts[str(row.verdict)] += 1

        if row.confidence is None:
            unknown_count += 1
            continue

        value = Decimal(str(row.confidence))
        if not value.is_finite():
            unknown_count += 1
            continue

        value = max(Decimal(0), min(Decimal(1), value))
        index = int((value * bins).to_integral_value(rounding=ROUND_FLOOR))
        index = min(max(index, 0), bins - 1)
        counts[index] += 1

    buckets = [
        ConfidenceBucket(
            lo=index / bins,
            hi=(index + 1) / bins,
            count=count,
        )
        for index, count in enumerate(counts)
    ]
    verdicts = [
        VerdictCount(verdict=verdict, count=count)
        for verdict, count in sorted(
            verdict_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return ConfidenceHistogramResponse(
        bins=bins,
        buckets=buckets,
        unknown_count=unknown_count,
        total=len(rows),
        verdicts=verdicts,
    )


app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine)

    with testing_session() as session:
        session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    confidence FLOAT,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    confidence,
                    verdict,
                    risk_tier,
                    last_assessed
                ) VALUES (
                    :server_id,
                    :confidence,
                    :verdict,
                    :risk_tier,
                    :last_assessed
                )
                """
            ),
            [
                {
                    "server_id": "registry-1",
                    "confidence": 0.05,
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "LOW",
                    "last_assessed": "2024-01-01T00:00:00",
                },
                {
                    "server_id": "registry-2",
                    "confidence": 0.5,
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "MEDIUM",
                    "last_assessed": "2024-01-02T00:00:00",
                },
                {
                    "server_id": "registry-3",
                    "confidence": 0.95,
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "HIGH",
                    "last_assessed": "2024-01-03T00:00:00",
                },
                {
                    "server_id": "registry-4",
                    "confidence": None,
                    "verdict": "TRUSTED_GENERAL",
                    "risk_tier": "LOW",
                    "last_assessed": "2024-01-04T00:00:00",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/confidence-histogram?bins=10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["buckets"]) == 10
    assert payload["unknown_count"] == 1
    assert any(bucket["count"] == 1 for bucket in payload["buckets"])
    print("PASS")


if __name__ == "__main__":
    _run_self_test()