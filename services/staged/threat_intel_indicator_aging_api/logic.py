from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


router = APIRouter()


def _as_utc(value: datetime | date | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso8601(value: datetime | date | str | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _is_aggregator(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return value is True or value == 1


def _bucket_for(fetched_at: datetime | date | str | None, now: datetime) -> str:
    timestamp = _as_utc(fetched_at)
    if timestamp is None:
        return "expired_90d"
    age = now - timestamp
    if age <= timedelta(days=1):
        return "fresh_24h"
    if age <= timedelta(days=7):
        return "aging_7d"
    if age <= timedelta(days=30):
        return "stale_30d"
    return "expired_90d"


def get_indicator_ageing(session: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
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

    grouped: dict[str, dict[str, Any]] = {}
    pulse_rows: dict[str, list[Any]] = {}

    for row in rows:
        indicator_type = str(row.indicator_type or "unknown")
        bucket = _bucket_for(row.fetched_at, now)
        aggregator = _is_aggregator(row.is_aggregator)

        group = grouped.setdefault(
            indicator_type,
            {
                "indicator_type": indicator_type,
                "total": 0,
                "aggregators": 0,
                "direct": 0,
                "pulse_ids": set(),
                "buckets": {
                    "fresh_24h": 0,
                    "aging_7d": 0,
                    "stale_30d": 0,
                    "expired_90d": 0,
                },
            },
        )
        group["total"] += 1
        group["aggregators" if aggregator else "direct"] += 1
        group["buckets"][bucket] += 1
        if row.pulse_id is not None:
            group["pulse_ids"].add(str(row.pulse_id))

        if row.pulse_id is not None:
            pulse_id = str(row.pulse_id)
            pulse_rows.setdefault(pulse_id, []).append(row)

    by_indicator_type: list[dict[str, Any]] = []
    for group in grouped.values():
        by_indicator_type.append(
            {
                "indicator_type": group["indicator_type"],
                "total": group["total"],
                "aggregators": group["aggregators"],
                "direct": group["direct"],
                "pulse_count": len(group["pulse_ids"]),
                "buckets": group["buckets"],
            }
        )
    by_indicator_type.sort(key=lambda item: item["indicator_type"])

    stale_pulses: list[dict[str, Any]] = []
    for pulse_id, refs in pulse_rows.items():
        timestamps = [_as_utc(ref.fetched_at) for ref in refs]
        valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
        oldest = min(valid_timestamps) if valid_timestamps else None
        has_expired_ref = any(
            _bucket_for(ref.fetched_at, now) == "expired_90d" for ref in refs
        )
        if not has_expired_ref:
            continue

        pulse_name = next(
            (ref.pulse_name for ref in refs if ref.pulse_name is not None),
            None,
        )
        stale_pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": str(pulse_name) if pulse_name is not None else None,
                "oldest_fetched_at": _iso8601(oldest),
                "indicator_count": len(refs),
            }
        )

    stale_pulses.sort(
        key=lambda pulse: (
            pulse["oldest_fetched_at"] is None,
            pulse["oldest_fetched_at"] or "",
            pulse["pulse_id"],
        )
    )

    total_indicators = sum(item["total"] for item in by_indicator_type)
    total_aggregators = sum(item["aggregators"] for item in by_indicator_type)
    total_expired = sum(
        item["buckets"]["expired_90d"] for item in by_indicator_type
    )

    return {
        "generated_at": now.isoformat(),
        "by_indicator_type": by_indicator_type,
        "totals": {
            "indicators": total_indicators,
            "aggregators": total_aggregators,
            "expired": total_expired,
        },
        "stale_pulses": stale_pulses,
    }


get_indicator_ageing_report = get_indicator_ageing
get_indicator_ageing_data = get_indicator_ageing
get_indicator_aging = get_indicator_ageing


@router.get("/api/threat_intel/indicator_ageing")
def indicator_ageing_endpoint(
    report: dict[str, Any] = Depends(get_indicator_ageing),
) -> dict[str, Any]:
    return report


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id=101,
                    pulse_name="Fresh IPv4 Pulse",
                    pulse_created=now - timedelta(days=1),
                    is_aggregator=False,
                    source="self-test",
                    source_url="https://example.test/101",
                    fetched_at=now - timedelta(hours=2),
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.2",
                    pulse_id=102,
                    pulse_name="Old IPv4 Pulse",
                    pulse_created=now - timedelta(days=60),
                    is_aggregator=False,
                    source="self-test",
                    source_url="https://example.test/102",
                    fetched_at=now - timedelta(days=60),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://one.example.test",
                    pulse_id=103,
                    pulse_name="URL Aggregator Pulse",
                    pulse_created=now - timedelta(days=3),
                    is_aggregator=True,
                    source="self-test",
                    source_url="https://one.example.test",
                    fetched_at=now - timedelta(days=3),
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://two.example.test",
                    pulse_id=103,
                    pulse_name="URL Aggregator Pulse",
                    pulse_created=now - timedelta(days=3),
                    is_aggregator=True,
                    source="self-test",
                    source_url="https://two.example.test",
                    fetched_at=now - timedelta(days=4),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        response = client.get("/api/threat_intel/indicator_ageing")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["by_indicator_type"]) == 2
        by_type = {item["indicator_type"]: item for item in payload["by_indicator_type"]}
        assert by_type["ipv4"]["total"] == 2
        assert by_type["ipv4"]["buckets"]["expired_90d"] == 1
        assert payload["totals"]["indicators"] == 4
        assert payload["totals"]["aggregators"] == 2
        assert payload["totals"]["expired"] == 1
        assert payload["stale_pulses"]

    print("PASS")