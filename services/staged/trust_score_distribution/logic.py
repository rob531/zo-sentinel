"""logic.py -- trust score distribution data/computation layer.

Reads the authoritative MCP server registry through the real application
SQLAlchemy session and returns a histogram, source breakdown, and unscored
row count.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_trust_score_distribution(
    db: Session, bucket: int = 10
) -> Dict[str, Any]:
    """Return the trust-score histogram and registry-source statistics."""
    if not isinstance(bucket, int) or isinstance(bucket, bool) or bucket <= 0:
        raise ValueError("bucket must be a positive integer")

    rows = db.execute(
        select(
            McpServerRegistry.trust_score,
            McpServerRegistry.registry_source,
        )
    ).all()

    bucket_width = float(bucket)
    bucket_count = max(1, int((100.0 / bucket_width) + 0.999999))
    buckets: List[Dict[str, Any]] = [
        {
            "lower": round(index * bucket_width, 6),
            "upper": round(min((index + 1) * bucket_width, 100.0), 6),
            "count": 0,
        }
        for index in range(bucket_count)
    ]

    source_counts: Dict[str, int] = defaultdict(int)
    source_scores: Dict[str, List[float]] = defaultdict(list)
    never_scored = 0
    total = 0

    for trust_score, registry_source in rows:
        total += 1
        source_name = registry_source or "unknown"
        source_counts[source_name] += 1

        if trust_score is None:
            never_scored += 1
            continue

        score = float(trust_score)
        source_scores[source_name].append(score)

        # Trust scores are expected to be in [0, 100]. Clamp malformed values
        # so every scored row remains represented in the histogram.
        score = min(100.0, max(0.0, score))
        index = min(
            bucket_count - 1,
            max(0, int(score / bucket_width)),
        )
        buckets[index]["count"] += 1

    by_source: List[Dict[str, Any]] = []
    for source_name in sorted(source_counts):
        scores = source_scores.get(source_name, [])
        mean_trust = round(sum(scores) / len(scores), 6) if scores else None
        by_source.append(
            {
                "registry_source": source_name,
                "count": source_counts[source_name],
                "mean_trust": mean_trust,
            }
        )

    return {
        "buckets": buckets,
        "by_source": by_source,
        "never_scored": never_scored,
        "total": total,
    }


# Compatibility aliases for staged service consumers.
trust_score_distribution = get_trust_score_distribution
compute_trust_score_distribution = get_trust_score_distribution
calculate_trust_score_distribution = get_trust_score_distribution
get_distribution = get_trust_score_distribution


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    seeded_rows = [
        ("registry-01", 0.0, "alpha", "low"),
        ("registry-02", 10.0, "alpha", "low"),
        ("registry-03", 20.0, "alpha", "medium"),
        ("registry-04", 30.0, "alpha", "medium"),
        ("registry-05", 40.0, "alpha", "high"),
        ("registry-06", 50.0, "beta", "high"),
        ("registry-07", 60.0, "beta", "medium"),
        ("registry-08", 70.0, "beta", "medium"),
        ("registry-09", 80.0, "beta", "low"),
        ("registry-10", 90.0, "beta", "low"),
        ("registry-11", 100.0, "beta", "high"),
        ("registry-12", None, "alpha", "unknown"),
    ]

    with TestSession() as session:
        for server_id, trust_score, registry_source, risk_tier in seeded_rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    registry_source=registry_source,
                    risk_tier=risk_tier,
                    trust_score=trust_score,
                    last_assessed=datetime.utcnow(),
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/trust-score-distribution")
    def trust_score_distribution_endpoint(
        bucket: int = 10,
        db: Session = Depends(get_session),
    ):
        return get_trust_score_distribution(db, bucket)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/trust-score-distribution",
            params={"bucket": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == len(seeded_rows)
        assert sum(item["count"] for item in payload["buckets"]) == (
            payload["total"] - payload["never_scored"]
        )
        assert payload["never_scored"] >= 1

    print("PASS")