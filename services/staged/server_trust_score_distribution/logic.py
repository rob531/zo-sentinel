from __future__ import annotations

from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = (
    ("0-20", 0, 20),
    ("21-40", 21, 40),
    ("41-60", 41, 60),
    ("61-80", 61, 80),
    ("81-100", 81, 100),
)

_HIGH_RISK_TIERS = {"high", "critical"}


def trust_score_distribution(db: Session) -> Dict[str, object]:
    """Return the registry trust-score histogram and tier agreement metrics."""
    rows = db.execute(
        select(
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
        )
    ).all()

    buckets: Dict[str, Dict[str, object]] = {
        label: {
            "bucket": label,
            "count": 0,
            "distinct_sources": 0,
            "tier_mix": {},
            "verdict_mix": {},
        }
        for label, _, _ in BUCKETS
    }
    sources_by_bucket: Dict[str, set] = {label: set() for label, _, _ in BUCKETS}
    tier_totals: Dict[str, int] = {}
    scored_servers = 0
    unscored_servers = 0
    disagreements = 0

    for score, risk_tier, verdict, registry_source in rows:
        if score is None:
            unscored_servers += 1
            continue

        scored_servers += 1
        score_value = float(score)
        if score_value <= 20:
            bucket_label = "0-20"
        elif score_value <= 40:
            bucket_label = "21-40"
        elif score_value <= 60:
            bucket_label = "41-60"
        elif score_value <= 80:
            bucket_label = "61-80"
        else:
            bucket_label = "81-100"

        bucket = buckets[bucket_label]
        bucket["count"] = int(bucket["count"]) + 1

        if registry_source is not None:
            sources_by_bucket[bucket_label].add(registry_source)

        tier_key = risk_tier if risk_tier is not None else "unknown"
        tier_mix = bucket["tier_mix"]
        tier_mix[tier_key] = int(tier_mix.get(tier_key, 0)) + 1
        tier_totals[tier_key] = tier_totals.get(tier_key, 0) + 1

        verdict_key = verdict if verdict is not None else "unknown"
        verdict_mix = bucket["verdict_mix"]
        verdict_mix[verdict_key] = int(verdict_mix.get(verdict_key, 0)) + 1

        if score_value >= 70 and (
            risk_tier is not None and str(risk_tier).strip().casefold() in _HIGH_RISK_TIERS
        ):
            disagreements += 1

    for label, _, _ in BUCKETS:
        buckets[label]["distinct_sources"] = len(sources_by_bucket[label])

    return {
        "total_servers": len(rows),
        "scored_servers": scored_servers,
        "unscored_servers": unscored_servers,
        "buckets": list(buckets.values()),
        "tier_totals": tier_totals,
        "trust_tier_disagreements": disagreements,
    }


get_trust_score_distribution = trust_score_distribution
compute_trust_score_distribution = trust_score_distribution


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/trust-score-distribution")
    def trust_score_distribution_endpoint(db: Session = Depends(get_session)):
        return trust_score_distribution(db)

    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-1",
                    name="test-1",
                    trust_score=10,
                    risk_tier="low",
                    verdict="trusted",
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="self-test-2",
                    name="test-2",
                    trust_score=50,
                    risk_tier="medium",
                    verdict="review",
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="self-test-3",
                    name="test-3",
                    trust_score=85,
                    risk_tier="high",
                    verdict="blocked",
                    registry_source="test",
                ),
                McpServerRegistry(
                    server_id="self-test-4",
                    name="test-4",
                    trust_score=None,
                    risk_tier="medium",
                    verdict="review",
                    registry_source="test",
                ),
            ]
        )
        db.commit()

    response = TestClient(that_app).get("/api/registry/trust-score-distribution")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scored_servers"] == 3
    assert payload["unscored_servers"] == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == 3
    print("PASS")