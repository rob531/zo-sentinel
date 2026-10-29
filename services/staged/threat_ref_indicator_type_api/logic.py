from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    return str(value)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def get_indicator_type_summary(
    session: Session = Depends(get_session),
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    active_session = db if db is not None else session
    if not hasattr(active_session, "execute"):
        raise TypeError("A database session is required")

    statement = select(
        ThreatIntelRef.indicator_type,
        ThreatIntelRef.is_aggregator,
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.source,
        ThreatIntelRef.fetched_at,
    )
    result = active_session.execute(statement)

    groups: dict[tuple[str, bool], dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    observed_types: set[str] = set()
    total_rows = 0

    for row in result:
        indicator_type = "" if row.indicator_type is None else str(row.indicator_type)
        is_aggregator = bool(row.is_aggregator)
        source = "" if row.source is None else str(row.source)
        key = (indicator_type, is_aggregator)

        group = groups.setdefault(
            key,
            {
                "rows": 0,
                "pulse_ids": set(),
                "fetched_at": [],
            },
        )
        group["rows"] += 1
        if row.pulse_id is not None:
            group["pulse_ids"].add(row.pulse_id)

        fetched_at = _as_datetime(row.fetched_at)
        if fetched_at is not None:
            group["fetched_at"].append(fetched_at)

        source_counts[source] += 1
        observed_types.add(indicator_type)
        total_rows += 1

    now = datetime.now(timezone.utc)
    indicator_types: list[dict[str, Any]] = []

    for (indicator_type, is_aggregator), group in sorted(groups.items()):
        fetched_times = group["fetched_at"]
        newest = max(fetched_times) if fetched_times else None
        oldest = min(fetched_times) if fetched_times else None

        if newest is not None:
            _newest_age_hours = (now - newest).total_seconds() / 3600
        if oldest is not None:
            _oldest_age_hours = (now - oldest).total_seconds() / 3600

        indicator_types.append(
            {
                "indicator_type": indicator_type,
                "is_aggregator": is_aggregator,
                "rows": group["rows"],
                "distinct_pulses": len(group["pulse_ids"]),
                "newest_fetched_at": _iso_timestamp(newest),
                "oldest_fetched_at": _iso_timestamp(oldest),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "indicator_types": indicator_types,
        "sources": [
            {"source": source, "rows": rows}
            for source, rows in sorted(source_counts.items())
        ],
        "observed_type_count": len(observed_types),
        "total_rows": total_rows,
    }


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    with TestSession() as test_session:
        fetched = datetime.now(timezone.utc)
        test_session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    pulse_created=fetched,
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/1",
                    fetched_at=fetched,
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    pulse_created=fetched,
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/2",
                    fetched_at=fetched,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    pulse_created=fetched,
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/1",
                    fetched_at=fetched,
                ),
            ]
        )
        test_session.commit()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/indicator-types")
    def indicator_types_endpoint(session: Session = Depends(get_session)):
        return get_indicator_type_summary(session)

    with TestClient(that_app) as client:
        response = client.get("/api/threat/indicator-types")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 3
        assert len(payload["indicator_types"]) == 2
        assert sum(item["rows"] for item in payload["indicator_types"]) == 3
        assert any(item["source"] == "alpha" and item["rows"] == 2 for item in payload["sources"])
        assert payload["observed_type_count"] == 2

    print("PASS")