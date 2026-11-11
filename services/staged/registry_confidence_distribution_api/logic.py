from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int


class VerdictConfidenceAggregate(BaseModel):
    count: int
    mean_confidence: float
    min: float
    max: float


class ConfidenceDistributionResponse(BaseModel):
    bucket_size: float
    total: int
    buckets: List[ConfidenceBucket]
    by_verdict: Dict[str, VerdictConfidenceAggregate]
    unknown_confidence: int = Field(default=0)


def _decimal(value: float | int | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _format_boundary(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." not in text:
        return f"{text}.0"
    return text.rstrip("0").rstrip(".") + "0"


def _bucket_index(confidence: float, bucket_size: float) -> int:
    value = max(Decimal("0"), _decimal(confidence))
    size = _decimal(bucket_size)
    index = int((value / size).to_integral_value(rounding="ROUND_FLOOR"))
    bucket_count = max(
        1,
        int(
            (Decimal("1") / size).to_integral_value(
                rounding=ROUND_CEILING
            )
        ),
    )
    return min(max(index, 0), bucket_count - 1)


def _bucket_labels(bucket_size: float) -> List[str]:
    size = _decimal(bucket_size)
    bucket_count = max(
        1,
        int(
            (Decimal("1") / size).to_integral_value(
                rounding=ROUND_CEILING
            )
        ),
    )
    labels: List[str] = []
    for index in range(bucket_count):
        start = Decimal(index) * size
        end = min(Decimal(index + 1) * size, Decimal("1"))
        labels.append(
            f"{_format_boundary(start)}-{_format_boundary(end)}"
        )
    return labels


def compute_confidence_distribution(
    rows: List[tuple],
    bucket_size: float = 0.1,
) -> ConfidenceDistributionResponse:
    labels = _bucket_labels(bucket_size)
    counts = [0] * len(labels)
    verdict_values: Dict[str, List[float]] = {}
    unknown_confidence = 0

    for row in rows:
        verdict = row[1]
        raw_confidence = row[2]
        confidence = (
            0.0
            if raw_confidence is None
            else float(raw_confidence)
        )
        verdict_key = str(verdict) if verdict is not None else "UNKNOWN"

        counts[_bucket_index(confidence, bucket_size)] += 1
        verdict_values.setdefault(verdict_key, []).append(confidence)

        if raw_confidence is None:
            unknown_confidence += 1

    by_verdict: Dict[str, VerdictConfidenceAggregate] = {}
    for verdict, confidences in verdict_values.items():
        by_verdict[verdict] = VerdictConfidenceAggregate(
            count=len(confidences),
            mean_confidence=sum(confidences) / len(confidences),
            min=min(confidences),
            max=max(confidences),
        )

    return ConfidenceDistributionResponse(
        bucket_size=bucket_size,
        total=len(rows),
        buckets=[
            ConfidenceBucket(bucket=label, count=count)
            for label, count in zip(labels, counts)
        ],
        by_verdict=by_verdict,
        unknown_confidence=unknown_confidence,
    )


@router.get(
    "/registry/confidence-distribution",
    response_model=ConfidenceDistributionResponse,
)
def get_confidence_distribution(
    verdict: Optional[str] = Query(default=None),
    min_confidence: Optional[float] = Query(default=None),
    bucket_size: float = Query(default=0.1, gt=0),
    session=Depends(get_session),
) -> ConfidenceDistributionResponse:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
    )

    if verdict is not None:
        statement = statement.where(McpServerRegistry.verdict == verdict)

    if min_confidence is not None:
        statement = statement.where(
            func.coalesce(McpServerRegistry.confidence, 0.0)
            >= min_confidence
        )

    rows = list(session.execute(statement).all())
    return compute_confidence_distribution(rows, bucket_size)


get_distribution = get_confidence_distribution
confidence_distribution = get_confidence_distribution


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="confidence-1",
                name="Confidence One",
                verdict="HIGH_RISK_ISOLATED",
                confidence=0.95,
            ),
            McpServerRegistry(
                server_id="confidence-2",
                name="Confidence Two",
                verdict="HIGH_RISK_ISOLATED",
                confidence=0.05,
            ),
            McpServerRegistry(
                server_id="confidence-3",
                name="Confidence Three",
                verdict="MEDIUM_RISK",
                confidence=0.5,
            ),
            McpServerRegistry(
                server_id="confidence-4",
                name="Confidence Four",
                verdict="LOW_RISK",
                confidence=None,
            ),
            McpServerRegistry(
                server_id="confidence-5",
                name="Confidence Five",
                verdict="HIGH_RISK_ISOLATED",
                confidence=None,
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-distribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 5
        assert "HIGH_RISK_ISOLATED" in payload["by_verdict"]
        assert payload["unknown_confidence"] == 2

    test_session.close()
    test_engine.dispose()
    print("PASS")