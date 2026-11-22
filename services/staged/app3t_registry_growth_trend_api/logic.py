"""Data and computation layer for the registry growth trend API."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


TIER_FIELDS = (
    "trusted_general",
    "caution_limited",
    "high_risk_isolated",
    "insufficient",
)


def _classify_tier(risk_tier: str | None) -> str:
    """Map registry risk tiers to the API's four tier buckets."""
    normalized = (risk_tier or "").strip().lower().replace("-", "_").replace(" ", "_")

    if normalized in {"trusted", "trusted_general", "general"}:
        return "trusted_general"
    if normalized in {"caution", "caution_limited", "limited"}:
        return "caution_limited"
    if normalized in {
        "high_risk",
        "high_risk_isolated",
        "isolated",
        "critical",
    }:
        return "high_risk_isolated"
    return "insufficient"


def get_growth_trend(db: Session, days: int) -> List[Dict[str, object]]:
    """Return daily new-server counts and risk-tier mix for the trend window."""
    if days < 1:
        raise ValueError("days must be at least 1")
    if days > 366:
        raise ValueError("days must not exceed 366")

    rows = db.execute(
        select(
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
    ).all()

    observations = []
    for first_seen, _last_seen, risk_tier, _registry_source in rows:
        if first_seen is None:
            continue
        observed_date = first_seen.date() if isinstance(first_seen, datetime) else first_seen
        observations.append((observed_date, _classify_tier(risk_tier)))

    end_date = max((item[0] for item in observations), default=date.today())
    start_date = end_date - timedelta(days=days - 1)
    series_dates = [start_date + timedelta(days=offset) for offset in range(days)]

    counts = {
        day: {
            "new_servers": 0,
            "trusted_general": 0,
            "caution_limited": 0,
            "high_risk_isolated": 0,
            "insufficient": 0,
        }
        for day in series_dates
    }

    for observed_date, tier in observations:
        if observed_date in counts:
            counts[observed_date]["new_servers"] += 1
            counts[observed_date][tier] += 1

    return [
        {"date": day.isoformat(), **counts[day]}
        for day in series_dates
    ]


def get_registry_growth_trend(db: Session, days: int) -> List[Dict[str, object]]:
    """Compatibility name for callers that use the service-qualified function."""
    return get_growth_trend(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with Session(test_engine) as test_db:
        base = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        seeded = [
            McpServerRegistry(
                server_id="growth-trend-test-1",
                name="growth-trend-test-1",
                url="https://example.com/1",
                first_seen=base - timedelta(days=2),
                last_seen=base - timedelta(days=2),
                risk_tier="trusted",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="growth-trend-test-2",
                name="growth-trend-test-2",
                url="https://example.com/2",
                first_seen=base - timedelta(days=1),
                last_seen=base - timedelta(days=1),
                risk_tier="caution",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="growth-trend-test-3",
                name="growth-trend-test-3",
                url="https://example.com/3",
                first_seen=base,
                last_seen=base,
                risk_tier="high_risk",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="growth-trend-test-4",
                name="growth-trend-test-4",
                url="https://example.com/4",
                first_seen=base,
                last_seen=base,
                risk_tier="trusted",
                registry_source="test",
            ),
        ]
        test_db.add_all(seeded)
        test_db.commit()

    def override_get_session():
        with Session(test_engine) as test_db:
            yield test_db

    that_app = FastAPI()

    @that_app.get("/api/registry/growth-trend")
    def growth_trend(
        days: int = Query(3, ge=1, le=366),
        db: Session = Depends(get_session),
    ):
        return {"days": days, "series": get_growth_trend(db, days)}

    that_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(that_app).get("/api/registry/growth-trend?days=3")
    payload = response.json()
    assert response.status_code == 200
    assert len(payload["series"]) == 3
    assert sum(point["new_servers"] for point in payload["series"]) == len(seeded)
    print("PASS")