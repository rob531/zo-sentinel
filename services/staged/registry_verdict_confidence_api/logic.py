from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


class VerdictConfidenceBucket(BaseModel):
    verdict: str
    count: int
    mean_confidence: float
    low_confidence_count: int


class VerdictConfidenceResponse(BaseModel):
    days: int
    buckets: list[VerdictConfidenceBucket]
    overall_mean_confidence: float


def _utc_datetime(value: datetime | date | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_verdict_confidence(days: int, session: Session) -> VerdictConfidenceResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.confidence,
            McpServerRegistry.last_assessed,
            McpServerRegistry.risk_tier,
        )
    ).all()

    grouped: dict[str, list[float]] = defaultdict(list)
    low_counts: dict[str, int] = defaultdict(int)
    all_confidences: list[float] = []

    for _server_id, verdict, confidence, last_assessed, _risk_tier in rows:
        assessed_at = _utc_datetime(last_assessed)
        if assessed_at is None or assessed_at < cutoff:
            continue

        verdict_name = str(verdict)
        if confidence is not None:
            numeric_confidence = float(confidence)
            grouped[verdict_name].append(numeric_confidence)
            all_confidences.append(numeric_confidence)
            if numeric_confidence < 0.5:
                low_counts[verdict_name] += 1
        else:
            grouped[verdict_name]

    buckets = [
        VerdictConfidenceBucket(
            verdict=verdict,
            count=len(confidences),
            mean_confidence=(
                round(sum(confidences) / len(confidences), 3) if confidences else 0.0
            ),
            low_confidence_count=low_counts[verdict],
        )
        for verdict, confidences in sorted(grouped.items())
    ]

    overall_mean = (
        round(sum(all_confidences) / len(all_confidences), 3)
        if all_confidences
        else 0.0
    )

    return VerdictConfidenceResponse(
        days=days,
        buckets=buckets,
        overall_mean_confidence=overall_mean,
    )


@router.get(
    "/registry/verdict-confidence",
    response_model=VerdictConfidenceResponse,
    summary="Summarize registry verdict confidence",
)
def verdict_confidence(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> VerdictConfidenceResponse:
    return get_verdict_confidence(days, session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import MetaData, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    McpServerRegistry.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "server_id": "confidence-test-trusted-1",
                    "verdict": "TRUSTED_GENERAL",
                    "confidence": 0.8,
                    "last_assessed": now,
                },
                {
                    "server_id": "confidence-test-trusted-2",
                    "verdict": "TRUSTED_GENERAL",
                    "confidence": 0.9,
                    "last_assessed": now,
                },
                {
                    "server_id": "confidence-test-caution-1",
                    "verdict": "CAUTION_LIMITED",
                    "confidence": 0.4,
                    "last_assessed": now,
                },
            ],
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-confidence", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        buckets = {bucket["verdict"]: bucket for bucket in payload["buckets"]}

        assert len(payload["buckets"]) == 2
        assert buckets["TRUSTED_GENERAL"]["count"] == 2
        assert buckets["TRUSTED_GENERAL"]["mean_confidence"] == 0.85
        assert buckets["TRUSTED_GENERAL"]["low_confidence_count"] == 0
        assert buckets["CAUTION_LIMITED"]["count"] == 1
        assert buckets["CAUTION_LIMITED"]["mean_confidence"] == 0.4
        assert buckets["CAUTION_LIMITED"]["low_confidence_count"] == 1
        assert payload["overall_mean_confidence"] == 0.7

    engine.dispose()
    print("PASS")