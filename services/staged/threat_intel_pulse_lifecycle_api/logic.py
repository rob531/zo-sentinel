from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    parsed = _as_utc(value)
    return parsed.isoformat() if parsed is not None else None


def get_pulse_lifecycle(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = session.execute(
        select(
            ThreatIntelRef.id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.source_url,
            ThreatIntelRef.fetched_at,
        )
    ).all()

    generated_at = datetime.now(timezone.utc)
    grouped: Dict[Any, Dict[str, Any]] = {}

    for row in rows:
        key = row.pulse_id
        group = grouped.get(key)
        if group is None:
            group = {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "first_seen": None,
                "last_seen": None,
                "indicator_count": 0,
                "aggregator_count": 0,
                "indicator_types": {},
                "pulse_created": None,
            }
            grouped[key] = group

        fetched_at = _as_utc(row.fetched_at)
        pulse_created = _as_utc(row.pulse_created)

        if fetched_at is not None:
            if group["first_seen"] is None or fetched_at < group["first_seen"]:
                group["first_seen"] = fetched_at
            if group["last_seen"] is None or fetched_at > group["last_seen"]:
                group["last_seen"] = fetched_at

        if pulse_created is not None:
            if (
                group["pulse_created"] is None
                or pulse_created < group["pulse_created"]
            ):
                group["pulse_created"] = pulse_created

        group["indicator_count"] += 1
        if bool(row.is_aggregator):
            group["aggregator_count"] += 1

        indicator_type = row.indicator_type or "unknown"
        group["indicator_types"][indicator_type] = (
            group["indicator_types"].get(indicator_type, 0) + 1
        )

    pulses: List[Dict[str, Any]] = []
    for group in grouped.values():
        age_origin = group["pulse_created"] or group["first_seen"]
        age_hours = (
            max(0.0, (generated_at - age_origin).total_seconds() / 3600.0)
            if age_origin is not None
            else None
        )
        pulses.append(
            {
                "pulse_id": group["pulse_id"],
                "pulse_name": group["pulse_name"],
                "first_seen": _iso(group["first_seen"]),
                "last_seen": _iso(group["last_seen"]),
                "indicator_count": group["indicator_count"],
                "aggregator_count": group["aggregator_count"],
                "indicator_types": dict(sorted(group["indicator_types"].items())),
                "age_hours": age_hours,
            }
        )

    pulses.sort(key=lambda item: str(item["pulse_id"]))
    return {
        "pulses": pulses,
        "total_pulses": len(pulses),
        "generated_at": generated_at.isoformat(),
    }


def list_pulses(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_pulse_lifecycle(session)


def get_pulses(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_pulse_lifecycle(session)


def get_threat_intel_pulse_lifecycle(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_pulse_lifecycle(session)


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
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-multi",
                    pulse_name="Multi indicator pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-multi",
                    pulse_name="Multi indicator pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/2",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="hash",
                    indicator_value="abc123",
                    pulse_id="pulse-multi",
                    pulse_name="Multi indicator pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/3",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="single.test",
                    pulse_id="pulse-single",
                    pulse_name="Single indicator pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/4",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    def override_session():
        with factory() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/pulses")
    def read_pulses(payload: Dict[str, Any] = Depends(get_pulse_lifecycle)):
        return payload

    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200
        body = response.json()
        assert len(body["pulses"]) == 2
        multi = next(
            pulse for pulse in body["pulses"] if pulse["pulse_id"] == "pulse-multi"
        )
        assert multi["indicator_count"] == 3
        assert multi["aggregator_count"] == 1

    print("PASS")