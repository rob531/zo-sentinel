from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import floor
from statistics import median
from typing import Any

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

__all__ = [
    "get_publish_lag",
    "get_advisory_publish_lag",
    "compute_publish_lag",
    "compute_publish_lag_report",
    "build_publish_lag_report",
    "publish_lag",
]

_BUCKETS = ("under_24h", "h24_72", "h72_168", "over_168h")


def _lag_hours_expression(session: Session):
    fetched_at = VulnAdvisory.fetched_at
    published_at = VulnAdvisory.published_at

    if session.get_bind().dialect.name == "sqlite":
        expression = (
            func.julianday(fetched_at) - func.julianday(published_at)
        ) * 24.0
    else:
        expression = func.extract("epoch", fetched_at - published_at) / 3600.0

    return cast(func.round(expression, 6), Float)


def _bucket_for(lag_hours: float) -> str:
    if lag_hours < 24.0:
        return "under_24h"
    if lag_hours < 72.0:
        return "h24_72"
    if lag_hours < 168.0:
        return "h72_168"
    return "over_168h"


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0

    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _lag_summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 6),
        "median": round(float(median(values)), 6),
        "p90": round(_percentile(values, 0.90), 6),
        "max": round(max(values), 6),
    }


def get_publish_lag(session: Session, days: int = 30) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)
    lag_expression = _lag_hours_expression(session)

    statement = (
        select(
            VulnAdvisory.feed,
            VulnAdvisory.ecosystem,
            lag_expression.label("lag_hours"),
        )
        .where(
            VulnAdvisory.published_at.is_not(None),
            VulnAdvisory.fetched_at.is_not(None),
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.feed, VulnAdvisory.ecosystem, VulnAdvisory.id)
    )

    feed_values: dict[str, list[float]] = {}
    ecosystem_values: dict[str, list[float]] = {}

    for feed, ecosystem, lag_hours in session.execute(statement).all():
        lag = float(lag_hours)
        feed_values.setdefault(str(feed), []).append(lag)
        ecosystem_values.setdefault(str(ecosystem or "unknown"), []).append(lag)

    feeds: list[dict[str, Any]] = []
    for feed in sorted(feed_values):
        values = feed_values[feed]
        stale_bucket = {bucket: 0 for bucket in _BUCKETS}
        for value in values:
            stale_bucket[_bucket_for(value)] += 1

        feeds.append(
            {
                "feed": feed,
                "count": len(values),
                "lag_hours": _lag_summary(values),
                "stale_bucket": stale_bucket,
            }
        )

    ecosystems = [
        {
            "ecosystem": ecosystem,
            "count": len(ecosystem_values[ecosystem]),
            "lag_hours": _lag_summary(ecosystem_values[ecosystem]),
        }
        for ecosystem in sorted(ecosystem_values)
    ]

    return {
        "days": days,
        "generated_at": generated_at.isoformat(),
        "feeds": feeds,
        "ecosystems": ecosystems,
    }


get_advisory_publish_lag = get_publish_lag
compute_publish_lag = get_publish_lag
compute_publish_lag_report = get_publish_lag
build_publish_lag_report = get_publish_lag
publish_lag = get_publish_lag


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
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    published = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    seeded_rows = [
        (1, "osv", "high", "pypi", "requests", published, published + timedelta(hours=6)),
        (2, "osv", "critical", "pypi", "flask", published, published + timedelta(hours=48)),
        (3, "osv", "medium", "npm", "lodash", published, published + timedelta(hours=200)),
    ]

    with TestSession() as session:
        for row_id, feed, severity, ecosystem, package, pub, fetched in seeded_rows:
            session.add(
                VulnAdvisory(
                    id=row_id,
                    feed=feed,
                    severity=severity,
                    ecosystem=ecosystem,
                    package=package,
                    published_at=pub,
                    fetched_at=fetched,
                    source_url=f"https://example.test/advisories/{row_id}",
                )
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/advisories/publish-lag")
    def test_publish_lag(
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_publish_lag(session, days)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/advisories/publish-lag", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload["feeds"], list)
        assert isinstance(payload["ecosystems"], list)
        assert payload["feeds"][0]["lag_hours"]["median"] == 48.0
        assert sum(payload["feeds"][0]["stale_bucket"].values()) == payload["feeds"][0]["count"]

    print("PASS")