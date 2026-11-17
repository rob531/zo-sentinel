from collections import defaultdict
from math import ceil, floor
from typing import Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int


class VerdictAggregate(BaseModel):
    count: int
    mean_confidence: float
    min: float
    max: float


class ConfidenceDistributionResponse(BaseModel):
    bucket_size: float
    total: int
    buckets: List[ConfidenceBucket]
    by_verdict: Dict[str, VerdictAggregate]
    unknown_confidence: int


router = APIRouter(prefix="/api")


def _format_number(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _bucket_label(index: int, bucket_size: float) -> str:
    start = index * bucket_size
    end = start + bucket_size
    return f"{_format_number(start)}-{_format_number(end)}"


def _confidence_distribution(
    db: Session,
    verdict: Optional[str],
    min_confidence: Optional[float],
    bucket_size: float,
) -> ConfidenceDistributionResponse:
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.confidence,
        )
    ).all()

    selected = []
    unknown_count = 0

    for row in rows:
        row_verdict = row.verdict if row.verdict is not None else "UNKNOWN"

        if verdict is not None and row_verdict != verdict:
            continue

        if row.confidence is None:
            confidence = 0.0
            unknown_count += 1
        else:
            confidence = float(row.confidence)

        if min_confidence is not None and confidence < min_confidence:
            continue

        selected.append((row_verdict, confidence))

    bucket_count = max(1, ceil(1.0 / bucket_size))
    maximum_index = bucket_count - 1
    bucket_counts = defaultdict(int)

    for _, confidence in selected:
        index = floor(max(0.0, confidence) / bucket_size)
        index = min(index, maximum_index)
        bucket_counts[index] += 1

    buckets = [
        ConfidenceBucket(
            bucket=_bucket_label(index, bucket_size),
            count=bucket_counts[index],
        )
        for index in range(bucket_count)
    ]

    verdict_values = defaultdict(list)
    for row_verdict, confidence in selected:
        verdict_values[row_verdict].append(confidence)

    by_verdict = {}
    for row_verdict, values in sorted(verdict_values.items()):
        by_verdict[row_verdict] = VerdictAggregate(
            count=len(values),
            mean_confidence=sum(values) / len(values),
            min=min(values),
            max=max(values),
        )

    return ConfidenceDistributionResponse(
        bucket_size=bucket_size,
        total=len(selected),
        buckets=buckets,
        by_verdict=by_verdict,
        unknown_confidence=unknown_count,
    )


@router.get(
    "/registry/confidence-distribution",
    response_model=ConfidenceDistributionResponse,
)
def get_confidence_distribution(
    verdict: Optional[str] = Query(default=None),
    min_confidence: Optional[float] = Query(default=None),
    bucket_size: float = Query(default=0.1, gt=0),
    db: Session = Depends(get_session),
) -> ConfidenceDistributionResponse:
    return _confidence_distribution(
        db=db,
        verdict=verdict,
        min_confidence=min_confidence,
        bucket_size=bucket_size,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    seed_rows = [
        McpServerRegistry(
            server_id="registry-1",
            name="registry-1",
            verdict="HIGH_RISK_ISOLATED",
            confidence=0.95,
        ),
        McpServerRegistry(
            server_id="registry-2",
            name="registry-2",
            verdict="HIGH_RISK_ISOLATED",
            confidence=0.55,
        ),
        McpServerRegistry(
            server_id="registry-3",
            name="registry-3",
            verdict="MEDIUM_RISK",
            confidence=None,
        ),
        McpServerRegistry(
            server_id="registry-4",
            name="registry-4",
            verdict="LOW_RISK",
            confidence=0.15,
        ),
        McpServerRegistry(
            server_id="registry-5",
            name="registry-5",
            verdict="LOW_RISK",
            confidence=None,
        ),
    ]

    with TestSession() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session() -> Iterator[Session]:
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/confidence-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
    assert "HIGH_RISK_ISOLATED" in payload["by_verdict"]
    assert payload["unknown_confidence"] == 2

    print("PASS")


if __name__ == "__main__":
    _run_self_test()