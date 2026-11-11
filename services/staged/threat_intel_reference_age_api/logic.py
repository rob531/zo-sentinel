from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _isoformat(value: datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_reference_age_by_source(session: Session) -> dict[str, Any]:
    source = func.coalesce(ThreatIntelRef.source, "unknown")
    statement = (
        select(
            source.label("source"),
            func.count(ThreatIntelRef.id).label("ref_count"),
            func.count(distinct(ThreatIntelRef.indicator_type)).label(
                "indicator_type_count"
            ),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
            func.count(
                case((ThreatIntelRef.is_aggregator.is_(True), 1))
            ).label("aggregator_ref_count"),
        )
        .group_by(source)
        .order_by(source)
    )

    now = datetime.now(timezone.utc)
    sources: list[dict[str, Any]] = []

    for row in session.execute(statement).all():
        newest = row.newest_fetched_at
        if isinstance(newest, datetime):
            staleness_hours = max(
                0.0,
                (now - _as_utc(newest)).total_seconds() / 3600.0,
            )
        else:
            staleness_hours = 0.0

        sources.append(
            {
                "source": str(row.source),
                "ref_count": int(row.ref_count or 0),
                "indicator_type_count": int(row.indicator_type_count or 0),
                "oldest_fetched_at": _isoformat(row.oldest_fetched_at),
                "newest_fetched_at": _isoformat(row.newest_fetched_at),
                "staleness_hours": float(staleness_hours),
                "aggregator_ref_count": int(row.aggregator_ref_count or 0),
            }
        )

    return {
        "now": now.isoformat(),
        "sources": sources,
        "total_refs": sum(item["ref_count"] for item in sources),
    }


def get_reference_age_stats(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


def get_threat_intel_reference_age(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


def get_reference_age(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


def get_reference_age_summary(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


def get_reference_age_data(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


def get_stats(session: Session) -> dict[str, Any]:
    return get_reference_age_by_source(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.metadata.create_all(
        test_engine, tables=[ThreatIntelRef.__table__]
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    oldest_iso = "2020-01-01T00:00:00"
    newest_iso = "2020-02-02T12:00:00"
    seed_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=datetime(2020, 1, 1),
            is_aggregator=True,
            source="alpha",
            source_url="https://alpha.example/ref",
            fetched_at=datetime.fromisoformat(oldest_iso),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=datetime(2020, 2, 2),
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/ref",
            fetched_at=datetime.fromisoformat(newest_iso),
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://beta.example/path",
            pulse_id="pulse-c",
            pulse_name="Pulse C",
            pulse_created=datetime(2020, 2, 3),
            is_aggregator=False,
            source="beta",
            source_url="https://beta.example/ref-2",
            fetched_at=datetime.fromisoformat("2020-03-03T06:30:00"),
        ),
    ]

    with TestSession() as seed_session:
        seed_session.add_all(seed_rows)
        seed_session.commit()

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/threat-intel/reference-age")
    def reference_age(session: Session = Depends(get_session)):
        return get_reference_age_by_source(session)

    response = TestClient(local_app).get("/api/threat-intel/reference-age")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_refs"] == 3
    beta = next(item for item in payload["sources"] if item["source"] == "beta")
    assert beta["ref_count"] == 2
    assert beta["newest_fetched_at"] == "2020-03-03T06:30:00"
    assert beta["staleness_hours"] >= 0

    print("PASS")