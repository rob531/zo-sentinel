from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_TRUST_BUCKETS = tuple((index * 10, (index + 1) * 10) for index in range(10))


def _bucket_for_score(score: float) -> tuple[str, int, int]:
    if not math.isfinite(score):
        return "unscored", 0, 0
    index = min(max(int(score // 10), 0), len(_TRUST_BUCKETS) - 1)
    low, high = _TRUST_BUCKETS[index]
    return f"{low}-{high}", low, high


def _group_label(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text or "unknown"


def build_trust_distribution(session: Session = Depends(get_session)) -> dict[str, Any]:
    server_count_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            func.count().label("server_count"),
        ).group_by(McpServerRegistry.server_id)
    ).all()
    total = sum(int(row.server_count) for row in server_count_rows)

    score_rows = session.execute(
        select(
            McpServerRegistry.trust_score,
            func.count().label("score_count"),
        ).group_by(McpServerRegistry.trust_score)
    ).all()

    bucket_counts = {f"{low}-{high}": 0 for low, high in _TRUST_BUCKETS}
    bucket_bounds = {f"{low}-{high}": (low, high) for low, high in _TRUST_BUCKETS}
    unscored_count = 0

    for row in score_rows:
        count = int(row.score_count)
        if row.trust_score is None:
            unscored_count += count
            continue
        bucket, _, _ = _bucket_for_score(float(row.trust_score))
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + count

    trust_buckets = [
        {
            "bucket": bucket,
            "lo": bucket_bounds[bucket][0],
            "hi": bucket_bounds[bucket][1],
            "count": bucket_counts[bucket],
        }
        for bucket in bucket_bounds
    ]
    if unscored_count:
        trust_buckets.append(
            {
                "bucket": "unscored",
                "lo": 0,
                "hi": 0,
                "count": unscored_count,
            }
        )

    verdict_rows = session.execute(
        select(
            McpServerRegistry.verdict,
            func.count().label("verdict_count"),
        ).group_by(McpServerRegistry.verdict)
    ).all()
    verdict_counts = {
        _group_label(row.verdict): int(row.verdict_count)
        for row in verdict_rows
    }

    risk_tier_rows = session.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count().label("risk_tier_count"),
        ).group_by(McpServerRegistry.risk_tier)
    ).all()
    risk_tier_counts = {
        _group_label(row.risk_tier): int(row.risk_tier_count)
        for row in risk_tier_rows
    }

    freshness_row = session.execute(
        select(
            func.max(McpServerRegistry.last_assessed).label("newest_assessed"),
            func.min(McpServerRegistry.last_assessed).label("oldest_assessed"),
        )
    ).one()
    stale_cutoff = datetime.utcnow() - timedelta(days=30)
    stale_row = session.execute(
        select(func.count().label("stale_count"))
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.last_assessed < stale_cutoff)
    ).one()

    return {
        "total": total,
        "trust_buckets": trust_buckets,
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "risk_tier_counts": dict(sorted(risk_tier_counts.items())),
        "freshness": {
            "newest_assessed": freshness_row.newest_assessed,
            "oldest_assessed": freshness_row.oldest_assessed,
            "stale_count": int(stale_row.stale_count),
        },
    }


build_registry_trust_distribution = build_trust_distribution
get_trust_distribution = build_trust_distribution
get_registry_trust_distribution = build_trust_distribution


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
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
                    name VARCHAR,
                    trust_score FLOAT,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    registry_source VARCHAR,
                    last_assessed DATETIME
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, trust_score, verdict, risk_tier,
                    registry_source, last_assessed
                ) VALUES (
                    :server_id, :name, :trust_score, :verdict, :risk_tier,
                    :registry_source, :last_assessed
                )
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Alpha",
                    "trust_score": 5,
                    "verdict": "trusted",
                    "risk_tier": "low",
                    "registry_source": "registry-a",
                    "last_assessed": now,
                },
                {
                    "server_id": "server-2",
                    "name": "Bravo",
                    "trust_score": 55,
                    "verdict": "review",
                    "risk_tier": "high",
                    "registry_source": "registry-a",
                    "last_assessed": now,
                },
                {
                    "server_id": "server-3",
                    "name": "Charlie",
                    "trust_score": 95,
                    "verdict": "trusted",
                    "risk_tier": "low",
                    "registry_source": "registry-b",
                    "last_assessed": now,
                },
                {
                    "server_id": "server-4",
                    "name": "Delta",
                    "trust_score": 25,
                    "verdict": "review",
                    "risk_tier": "high",
                    "registry_source": "registry-b",
                    "last_assessed": now,
                },
                {
                    "server_id": "server-5",
                    "name": "Echo",
                    "trust_score": 75,
                    "verdict": "trusted",
                    "risk_tier": "low",
                    "registry_source": "registry-a",
                    "last_assessed": now,
                },
                {
                    "server_id": "server-6",
                    "name": "Foxtrot",
                    "trust_score": None,
                    "verdict": "review",
                    "risk_tier": "high",
                    "registry_source": "registry-b",
                    "last_assessed": now - timedelta(days=45),
                },
            ],
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()

    @test_app.get("/api/registry/trust-distribution")
    def test_registry_trust_distribution(session: Session = Depends(get_session)):
        return build_trust_distribution(session)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/registry/trust-distribution")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert any(
        bucket["bucket"] == "unscored" and bucket["count"] == 1
        for bucket in payload["trust_buckets"]
    )
    assert "trusted" in payload["verdict_counts"]
    assert "review" in payload["verdict_counts"]
    assert payload["freshness"]["stale_count"] >= 1
    print("PASS")