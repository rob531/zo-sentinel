from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import *

router = APIRouter()


class ConfidenceBucketResponse(BaseModel):
    bucket: str
    count: int
    avg_trust_score: float


class RegistryConfidenceBucketsResponse(BaseModel):
    buckets: List[ConfidenceBucketResponse]
    total: int
    low_confidence_count: int
    as_of: datetime


_BUCKET_LABELS = (
    "0.0-0.25",
    "0.25-0.5",
    "0.5-0.75",
    "0.75-1.0",
    "unknown",
)


def _bucket_for_confidence(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence < 0.25:
        return "0.0-0.25"
    if confidence < 0.5:
        return "0.25-0.5"
    if confidence < 0.75:
        return "0.5-0.75"
    return "0.75-1.0"


@router.get(
    "/registry/confidence-buckets",
    response_model=RegistryConfidenceBucketsResponse,
)
def get_registry_confidence_buckets(
    session: Session = Depends(get_session),
) -> RegistryConfidenceBucketsResponse:
    rows = session.execute(
        select(
            McpServerRegistry.confidence,
            McpServerRegistry.trust_score,
        )
    ).all()

    counts = {label: 0 for label in _BUCKET_LABELS}
    trust_totals = {label: 0.0 for label in _BUCKET_LABELS}
    trust_counts = {label: 0 for label in _BUCKET_LABELS}

    for confidence, trust_score in rows:
        bucket = _bucket_for_confidence(confidence)
        counts[bucket] += 1
        if trust_score is not None:
            trust_totals[bucket] += float(trust_score)
            trust_counts[bucket] += 1

    buckets = [
        ConfidenceBucketResponse(
            bucket=label,
            count=counts[label],
            avg_trust_score=(
                trust_totals[label] / trust_counts[label]
                if trust_counts[label]
                else 0.0
            ),
        )
        for label in _BUCKET_LABELS
    ]

    return RegistryConfidenceBucketsResponse(
        buckets=buckets,
        total=len(rows),
        low_confidence_count=counts["0.0-0.25"] + counts["0.25-0.5"],
        as_of=datetime.now(timezone.utc),
    )


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

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    trust_score FLOAT,
                    confidence FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, trust_score, confidence)
                VALUES
                    (:server_id, :trust_score, :confidence)
                """
            ),
            [
                {
                    "server_id": "confidence-test-1",
                    "trust_score": 0.20,
                    "confidence": 0.10,
                },
                {
                    "server_id": "confidence-test-2",
                    "trust_score": 0.40,
                    "confidence": 0.30,
                },
                {
                    "server_id": "confidence-test-3",
                    "trust_score": 0.80,
                    "confidence": 0.80,
                },
                {
                    "server_id": "confidence-test-4",
                    "trust_score": 0.50,
                    "confidence": None,
                },
            ],
        )

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/confidence-buckets")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert next(
        bucket["count"]
        for bucket in payload["buckets"]
        if bucket["bucket"] == "unknown"
    ) == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]

    test_engine.dispose()
    print("PASS")