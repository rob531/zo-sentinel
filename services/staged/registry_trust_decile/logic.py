"""logic.py -- registry trust decile computation over the real application data layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_DECILE_LABELS = tuple(f"{lower}-{lower + 9}" for lower in range(0, 100, 10))


def _bucket_for_score(score: Any) -> Optional[str]:
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value < 0:
        value = 0
    if value > 100:
        value = 100
    return _DECILE_LABELS[min(int(value // 10), 9)]


def get_trust_deciles(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Return registry server counts by trust-score decile and risk tier."""
    query = select(McpServerRegistry.trust_score, McpServerRegistry.risk_tier)
    if registry_source is not None:
        query = query.where(McpServerRegistry.registry_source == registry_source)

    rows = db.execute(query).all()
    buckets: Dict[str, Dict[str, Any]] = {
        label: {"bucket": label, "count": 0, "tiers": {}}
        for label in _DECILE_LABELS
    }
    total = 0

    for score, risk_tier in rows:
        total += 1
        bucket_label = _bucket_for_score(score)
        if bucket_label is None:
            continue
        bucket = buckets[bucket_label]
        bucket["count"] += 1
        tier_name = risk_tier or "unknown"
        bucket["tiers"][tier_name] = bucket["tiers"].get(tier_name, 0) + 1

    return {
        "deciles": [buckets[label] for label in _DECILE_LABELS],
        "total": total,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_deciles(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the shorter function name."""
    return get_trust_deciles(db, registry_source=registry_source)


def get_registry_trust_deciles(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility wrapper for the service-specific function name."""
    return get_trust_deciles(db, registry_source=registry_source)


def trust_decile_histogram(
    db: Session, registry_source: Optional[str] = None
) -> Dict[str, Any]:
    """Compatibility wrapper exposing the decile computation."""
    return get_trust_deciles(db, registry_source=registry_source)


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestingSession = sessionmaker(bind=engine)

    seeded_scores = [0, 9, 10, 19, 20, 29, 30, 49, 70, 89, 90, 100]
    with TestingSession() as session:
        for index, score in enumerate(seeded_scores):
            session.add(
                McpServerRegistry(
                    server_id=f"self-test-{index}",
                    name=f"self-test-{index}",
                    trust_score=score,
                    risk_tier="HIGH_RISK_ISOLATED" if index % 2 == 0 else "STANDARD",
                    registry_source="self-test",
                    verdict="approved",
                )
            )
        session.commit()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/api/registry/trust-decile")
    def trust_decile_endpoint(
        db: Session = __import__("fastapi").Depends(get_session),
    ):
        return get_trust_deciles(db)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/registry/trust-decile")
    assert response.status_code == 200
    payload = response.json()
    assert sum(item["count"] for item in payload["deciles"]) == len(seeded_scores)
    assert payload["total"] == len(seeded_scores)
    assert next(item for item in payload["deciles"] if item["bucket"] == "0-9")["count"] == 2
    assert all("tiers" in item for item in payload["deciles"])
    print("PASS")