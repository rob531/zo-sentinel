from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from app.db import get_session
from app.models import ThreatIntelRef


_BAND_ORDER = ("fresh", "aging", "stale", "ancient")


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: Any) -> Optional[str]:
    parsed = _coerce_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _staleness_band(age_days: float) -> str:
    if age_days <= 2:
        return "fresh"
    if age_days <= 7:
        return "aging"
    if age_days <= 30:
        return "stale"
    return "ancient"


def get_threat_intel_pulses_age(
    session=Depends(get_session),
    as_of: Optional[datetime] = None,
    min_age_days: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    as_of_value = _coerce_datetime(as_of) or datetime.now(timezone.utc)
    minimum_age = max(0, int(min_age_days))
    maximum_rows = min(max(0, int(limit)), 500)

    rows = session.query(ThreatIntelRef).all()
    grouped: Dict[Any, Dict[str, Any]] = {}

    for row in rows:
        key = (row.pulse_id, row.pulse_name)
        bucket = grouped.setdefault(
            key,
            {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "indicator_count": 0,
                "indicator_types": set(),
                "is_aggregator": False,
                "pulse_created": _iso_timestamp(row.pulse_created),
                "oldest_fetched_at_value": None,
                "newest_fetched_at_value": None,
            },
        )

        fetched_at = _coerce_datetime(row.fetched_at)
        if fetched_at is not None:
            oldest = bucket["oldest_fetched_at_value"]
            newest = bucket["newest_fetched_at_value"]
            if oldest is None or fetched_at < oldest:
                bucket["oldest_fetched_at_value"] = fetched_at
            if newest is None or fetched_at > newest:
                bucket["newest_fetched_at_value"] = fetched_at

        bucket["indicator_count"] += 1
        if row.indicator_type is not None:
            bucket["indicator_types"].add(row.indicator_type)
        if bool(row.is_aggregator):
            bucket["is_aggregator"] = True

    pulses: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        newest = bucket["newest_fetched_at_value"]
        if newest is None:
            age_days = 0.0
            oldest_iso = None
            newest_iso = None
        else:
            age_days = (as_of_value - newest).total_seconds() / 86400.0
            oldest_iso = _iso_timestamp(bucket["oldest_fetched_at_value"])
            newest_iso = _iso_timestamp(newest)

        if age_days < minimum_age:
            continue

        pulses.append(
            {
                "pulse_id": bucket["pulse_id"],
                "pulse_name": bucket["pulse_name"],
                "indicator_count": bucket["indicator_count"],
                "indicator_type_count": len(bucket["indicator_types"]),
                "is_aggregator": bucket["is_aggregator"],
                "pulse_created": bucket["pulse_created"],
                "oldest_fetched_at": oldest_iso,
                "newest_fetched_at": newest_iso,
                "age_days": age_days,
                "staleness_band": _staleness_band(age_days),
            }
        )

    pulses.sort(
        key=lambda item: (
            -item["age_days"],
            str(item["pulse_id"]),
            str(item["pulse_name"]),
        )
    )
    pulses = pulses[:maximum_rows]

    bands = {band: 0 for band in _BAND_ORDER}
    for pulse in pulses:
        bands[pulse["staleness_band"]] += 1

    return {
        "as_of": as_of_value.isoformat(),
        "total_pulses": len(pulses),
        "total_indicators": sum(pulse["indicator_count"] for pulse in pulses),
        "bands": bands,
        "pulses": pulses,
    }


get_threat_intel_pulse_age = get_threat_intel_pulses_age
get_threat_intel_pulse_ages = get_threat_intel_pulses_age
get_pulses_age = get_threat_intel_pulses_age
get_pulse_ages = get_threat_intel_pulses_age
list_pulse_ages = get_threat_intel_pulses_age


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
    ThreatIntelRef.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    testing_session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-fresh",
                pulse_name="Fresh pulse",
                pulse_created=datetime(2025, 1, 18, tzinfo=timezone.utc),
                is_aggregator=False,
                source="test",
                source_url="https://example.invalid/fresh",
                fetched_at=datetime(2025, 1, 18, 12, 0, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="example.invalid",
                pulse_id="pulse-stale",
                pulse_name="Stale pulse",
                pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                is_aggregator=True,
                source="test",
                source_url="https://example.invalid/stale",
                fetched_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-stale",
                pulse_name="Stale pulse",
                pulse_created=datetime(2025, 1, 1, tzinfo=timezone.utc),
                is_aggregator=True,
                source="test",
                source_url="https://example.invalid/stale",
                fetched_at=datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
            ),
        ]
    )
    testing_session.commit()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = lambda: testing_session

    @local_app.get("/api/threat-intel/pulses/age")
    def pulse_age_endpoint(
        as_of: Optional[datetime] = None,
        min_age_days: int = 0,
        limit: int = 100,
        session=Depends(get_session),
    ):
        return get_threat_intel_pulses_age(
            session=session,
            as_of=as_of,
            min_age_days=min_age_days,
            limit=limit,
        )

    with TestClient(local_app) as client:
        response = client.get(
            "/api/threat-intel/pulses/age",
            params={"as_of": "2025-01-20T00:00:00+00:00"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_pulses"] == 2
        assert payload["total_indicators"] == 3
        assert payload["bands"]["fresh"] == 1
        assert payload["bands"]["stale"] == 1
        stale_pulse = next(
            pulse for pulse in payload["pulses"] if pulse["pulse_id"] == "pulse-stale"
        )
        assert stale_pulse["age_days"] == 17.5
        assert stale_pulse["staleness_band"] == "stale"

    testing_session.close()
    engine.dispose()
    print("PASS")