"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the authoritative registry through the real app.db SQLAlchemy session and
performs the cohort grouping in Python.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def _clamp_days(days: int) -> int:
    """Clamp the requested lookback to the supported range."""
    return max(1, min(365, int(days)))


def _as_utc_date(value: Any) -> Optional[date]:
    """Convert a registry timestamp to a UTC date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return _as_utc_date(parsed)
    return None


def get_first_seen_cohorts(
    db: Session,
    days: int = 90,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return registry cohorts grouped by the ISO week of first_seen.

    The database query is deliberately limited to the registry columns needed
    by this endpoint.  Date filtering and ISO-week bucketing are performed in
    Python so the behavior is independent of the database's timestamp type.
    """
    lookback_days = _clamp_days(days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.first_seen,
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        statement = statement.where(McpServerRegistry.registry_source == registry_source)

    rows = db.execute(statement).all()

    cohorts: Dict[str, Dict[str, Any]] = {}
    for _server_id, first_seen, row_source, risk_tier in rows:
        first_seen_date = _as_utc_date(first_seen)
        if first_seen_date is None:
            continue
        if first_seen_date < cutoff:
            continue

        iso_year, iso_week, _ = first_seen_date.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        cohort = cohorts.setdefault(
            week,
            {
                "week": week,
                "cohort_size": 0,
                "sources": Counter(),
                "tier_counts": Counter(),
            },
        )
        cohort["cohort_size"] += 1
        cohort["sources"][str(row_source) if row_source is not None else "unknown"] += 1
        cohort["tier_counts"][str(risk_tier) if risk_tier is not None else "unknown"] += 1

    output: list[Dict[str, Any]] = []
    for week in sorted(cohorts):
        cohort = cohorts[week]
        output.append(
            {
                "week": week,
                "cohort_size": int(cohort["cohort_size"]),
                "sources": [
                    {"registry_source": source, "count": int(count)}
                    for source, count in sorted(cohort["sources"].items())
                ],
                "tier_counts": {
                    tier: int(count)
                    for tier, count in sorted(cohort["tier_counts"].items())
                },
            }
        )

    return {
        "total_servers": sum(int(cohort["cohort_size"]) for cohort in output),
        "cohorts": output,
    }


def get_registry_first_seen_cohorts(
    db: Session,
    days: int = 90,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the registry-qualified name."""
    return get_first_seen_cohorts(db, days=days, registry_source=registry_source)


def first_seen_cohorts(
    db: Session,
    days: int = 90,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the concise cohort name."""
    return get_first_seen_cohorts(db, days=days, registry_source=registry_source)


def _self_test() -> None:
    """Exercise the endpoint against the real ORM model in a local test DB."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)

    today = datetime.now(timezone.utc).date()
    dates = [today, today, today, today - timedelta(days=7), today - timedelta(days=7)]
    sources = ["source-a", "source-a", "source-b", "source-a", "source-b"]
    tiers = ["high", "medium", "low", "medium", "low"]

    with local_session() as session:
        for index, (first_seen, source, tier) in enumerate(zip(dates, sources, tiers)):
            session.add(
                McpServerRegistry(
                    server_id=f"self-test-{index}",
                    first_seen=datetime.combine(first_seen, datetime.min.time()),
                    registry_source=source,
                    risk_tier=tier,
                )
            )
        session.commit()

        local_app = FastAPI()

        @local_app.get("/api/registry/first-seen-cohorts")
        def registry_first_seen_cohorts(
            db: Session = Depends(get_session),
            days: int = 90,
            registry_source: Optional[str] = None,
        ) -> Dict[str, Any]:
            return get_first_seen_cohorts(db, days, registry_source)

        def override_get_session():
            with local_session() as session:
                yield session

        local_app.dependency_overrides[get_session] = override_get_session
        client = TestClient(local_app)
        response = client.get("/api/registry/first-seen-cohorts")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["cohorts"]) == 2
        assert sum(cohort["cohort_size"] for cohort in payload["cohorts"]) == 5
        assert all(
            sum(cohort["tier_counts"].values()) == cohort["cohort_size"]
            for cohort in payload["cohorts"]
        )

    print("PASS")


if __name__ == "__main__":
    _self_test()