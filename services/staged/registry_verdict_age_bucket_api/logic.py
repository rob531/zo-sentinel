from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_ORDER = (
    "0-1d",
    "1-3d",
    "3-7d",
    "7-14d",
    "14-30d",
    "30d+",
    "never",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age: timedelta) -> str:
    if age < timedelta(days=1):
        return "0-1d"
    if age < timedelta(days=3):
        return "1-3d"
    if age < timedelta(days=7):
        return "3-7d"
    if age < timedelta(days=14):
        return "7-14d"
    if age < timedelta(days=30):
        return "14-30d"
    return "30d+"


def get_verdict_age_buckets(session: Any) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.confidence,
        )
    ).mappings()

    buckets: dict[str, dict[str, Any]] = {
        name: {
            "bucket": name,
            "count": 0,
            "verdict_counts": {},
            "_confidences": [],
        }
        for name in BUCKET_ORDER
    }

    for row in rows:
        last_assessed = row["last_assessed"]
        if last_assessed is None:
            bucket_name = "never"
        else:
            age = max(now - _as_utc(last_assessed), timedelta(0))
            bucket_name = _bucket_for_age(age)

        bucket = buckets[bucket_name]
        bucket["count"] += 1

        verdict = row["verdict"]
        verdict_name = str(verdict) if verdict is not None else "UNKNOWN"
        bucket["verdict_counts"][verdict_name] = (
            bucket["verdict_counts"].get(verdict_name, 0) + 1
        )

        confidence = row["confidence"]
        if confidence is not None:
            bucket["_confidences"].append(float(confidence))

    results: list[dict[str, Any]] = []
    for name in BUCKET_ORDER:
        bucket = buckets[name]
        confidences = bucket.pop("_confidences")
        bucket["avg_confidence"] = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        results.append(bucket)

    return results


def get_verdict_age_distribution(session: Any) -> list[dict[str, Any]]:
    return get_verdict_age_buckets(session)


def get_registry_verdict_age_buckets(session: Any) -> list[dict[str, Any]]:
    return get_verdict_age_buckets(session)


def run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    test_rows = [
        McpServerRegistry(
            server_id="self-test-0d",
            verdict="TRUSTED_GENERAL",
            last_assessed=now,
            last_seen=now,
            risk_tier="LOW",
            confidence=0.95,
        ),
        McpServerRegistry(
            server_id="self-test-2d",
            verdict="TRUSTED_LIMITED",
            last_assessed=now - timedelta(days=2),
            last_seen=now,
            risk_tier="MEDIUM",
            confidence=0.75,
        ),
        McpServerRegistry(
            server_id="self-test-10d",
            verdict="NEEDS_REVIEW",
            last_assessed=now - timedelta(days=10),
            last_seen=now,
            risk_tier="HIGH",
            confidence=0.40,
        ),
        McpServerRegistry(
            server_id="self-test-never",
            verdict="UNKNOWN",
            last_assessed=None,
            last_seen=now,
            risk_tier="UNKNOWN",
            confidence=None,
        ),
    ]

    with test_session_factory() as session:
        session.add_all(test_rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/verdict-age")
    def verdict_age_endpoint(session: Any = Depends(get_session)):
        buckets = get_verdict_age_buckets(session)
        return {
            "generated_at": datetime.now(timezone.utc),
            "total_servers": sum(bucket["count"] for bucket in buckets),
            "buckets": buckets,
        }

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-age")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        by_name = {bucket["bucket"]: bucket for bucket in payload["buckets"]}
        assert by_name["0-1d"]["count"] == 1
        assert "never" in by_name

    print("PASS")


if __name__ == "__main__":
    run_self_test()