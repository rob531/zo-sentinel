from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


UTC = timezone.utc
SECONDS_PER_DAY = 24 * 60 * 60


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None
    return None


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    parsed = _as_datetime(value)
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _freshness_bucket(elapsed_seconds: float) -> tuple[int, str]:
    if elapsed_seconds < 0:
        elapsed_seconds = 0
    stale_days = int(elapsed_seconds // SECONDS_PER_DAY)
    if elapsed_seconds < SECONDS_PER_DAY:
        bucket = "<1d"
    elif elapsed_seconds <= 7 * SECONDS_PER_DAY:
        bucket = "1-7d"
    elif elapsed_seconds <= 30 * SECONDS_PER_DAY:
        bucket = "7-30d"
    else:
        bucket = ">30d"
    return stale_days, bucket


def get_indicator_lifecycle(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    active_session = session if session is not None else db
    if active_session is None:
        raise ValueError("A database session is required")

    aggregator_count = func.coalesce(
        func.sum(
            case(
                (ThreatIntelRef.is_aggregator.is_(True), 1),
                else_=0,
            )
        ),
        0,
    )

    statement = (
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("total"),
            func.count(distinct(ThreatIntelRef.indicator_value)).label("distinct_values"),
            func.count(distinct(ThreatIntelRef.pulse_id)).label("distinct_pulses"),
            aggregator_count.label("aggregator_count"),
            func.max(ThreatIntelRef.pulse_created).label("latest_pulse_created"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    )

    now = datetime.now(UTC)
    by_type: list[dict[str, Any]] = []
    total_indicators = 0

    for row in active_session.execute(statement):
        latest_fetched = _as_datetime(row.latest_fetched_at)
        if latest_fetched is None:
            stale_days = 0
            freshness_bucket = "<1d"
        else:
            if latest_fetched.tzinfo is None:
                latest_fetched = latest_fetched.replace(tzinfo=UTC)
            stale_days, freshness_bucket = _freshness_bucket(
                (now - latest_fetched).total_seconds()
            )

        total = int(row.total or 0)
        total_indicators += total
        by_type.append(
            {
                "indicator_type": str(row.indicator_type),
                "total": total,
                "distinct_values": int(row.distinct_values or 0),
                "distinct_pulses": int(row.distinct_pulses or 0),
                "aggregator_count": int(row.aggregator_count or 0),
                "latest_pulse_created": _iso_timestamp(row.latest_pulse_created),
                "latest_fetched_at": _iso_timestamp(row.latest_fetched_at),
                "stale_days": stale_days,
                "freshness_bucket": freshness_bucket,
            }
        )

    return {
        "total_indicators": total_indicators,
        "by_type": by_type,
    }


def build_indicator_lifecycle(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_indicator_lifecycle(session=session, db=db)


def compute_indicator_lifecycle(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_indicator_lifecycle(session=session, db=db)


def get_indicator_lifecycle_summary(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_indicator_lifecycle(session=session, db=db)


def get_lifecycle_summary(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_indicator_lifecycle(session=session, db=db)


def build_lifecycle_summary(
    session: Optional[Session] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    return get_indicator_lifecycle(session=session, db=db)


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
    ThreatIntelRef.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(UTC)
    with TestingSession() as test_session:
        for index in range(4):
            test_session.add(
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value=f"192.0.2.{index + 1}",
                    pulse_id=index + 1,
                    pulse_name=f"IPv4 pulse {index + 1}",
                    pulse_created=now - timedelta(hours=index + 1),
                    is_aggregator=index == 0,
                    source="test",
                    source_url=f"https://example.test/ipv4/{index + 1}",
                    fetched_at=now - timedelta(hours=index + 1),
                )
            )
        for index in range(4):
            test_session.add(
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value=f"example{index + 1}.test",
                    pulse_id=100 + index,
                    pulse_name=f"Domain pulse {index + 1}",
                    pulse_created=now - timedelta(days=index + 1),
                    is_aggregator=False,
                    source="test",
                    source_url=f"https://example.test/domain/{index + 1}",
                    fetched_at=now - timedelta(days=index + 1),
                )
            )
        for index in range(4):
            fetched_at = now - timedelta(days=40 + index)
            test_session.add(
                ThreatIntelRef(
                    indicator_type="sha256",
                    indicator_value=f"{index + 1:064x}",
                    pulse_id=200 + index,
                    pulse_name=f"SHA256 pulse {index + 1}",
                    pulse_created=now - timedelta(days=40 + index),
                    is_aggregator=False,
                    source="test",
                    source_url=f"https://example.test/sha256/{index + 1}",
                    fetched_at=fetched_at,
                )
            )
        test_session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/indicator-lifecycle")
    def indicator_lifecycle_endpoint(session: Session = Depends(get_session)):
        return get_indicator_lifecycle(session=session)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/indicator-lifecycle")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 12
        assert len(payload["by_type"]) == 3
        by_type = {item["indicator_type"]: item for item in payload["by_type"]}
        assert by_type["ipv4"]["aggregator_count"] == 1
        assert by_type["sha256"]["freshness_bucket"] == ">30d"

    print("PASS")