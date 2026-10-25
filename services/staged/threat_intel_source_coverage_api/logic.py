from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends

from app.db import get_session
from app.models import ThreatIntelRef


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_threat_intel_coverage(db=Depends(get_session)) -> Dict[str, Any]:
    rows = (
        db.query(
            ThreatIntelRef.source,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.fetched_at,
            ThreatIntelRef.source_url,
        )
        .all()
    )

    source_groups: Dict[Any, Dict[str, Any]] = {}
    indicator_counts: Dict[Any, int] = defaultdict(int)
    total_indicators = 0
    aggregator_count = 0
    stale_over_24h = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    for row in rows:
        source = row.source
        is_aggregator = row.is_aggregator is True
        fetched_at = _coerce_datetime(row.fetched_at)

        total_indicators += 1
        indicator_counts[row.indicator_type] += 1
        if is_aggregator:
            aggregator_count += 1
        if fetched_at is not None and fetched_at < cutoff:
            stale_over_24h += 1

        group = source_groups.setdefault(
            source,
            {
                "is_aggregator": False,
                "pulse_ids": set(),
                "newest_fetched_at": None,
            },
        )
        if is_aggregator:
            group["is_aggregator"] = True
        if row.pulse_id is not None:
            group["pulse_ids"].add(row.pulse_id)
        if fetched_at is not None:
            newest = group["newest_fetched_at"]
            if newest is None or fetched_at > newest:
                group["newest_fetched_at"] = fetched_at

    by_source: List[Dict[str, Any]] = []
    for source, group in source_groups.items():
        by_source.append(
            {
                "source": source,
                "count": sum(1 for row in rows if row.source == source),
                "is_aggregator": bool(group["is_aggregator"]),
                "distinct_pulses": len(group["pulse_ids"]),
                "newest_fetched_at": _iso_timestamp(group["newest_fetched_at"]),
            }
        )

    by_source.sort(
        key=lambda item: (
            -item["count"],
            "" if item["source"] is None else str(item["source"]),
        )
    )

    by_indicator_type = [
        {"indicator_type": indicator_type, "count": count}
        for indicator_type, count in indicator_counts.items()
    ]
    by_indicator_type.sort(
        key=lambda item: (
            -item["count"],
            "" if item["indicator_type"] is None else str(item["indicator_type"]),
        )
    )

    return {
        "total_indicators": total_indicators,
        "by_source": by_source,
        "aggregator_count": aggregator_count,
        "non_aggregator_count": total_indicators - aggregator_count,
        "stale_over_24h": stale_over_24h,
        "by_indicator_type": by_indicator_type,
    }


def _self_test() -> None:
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
    ThreatIntelRef.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    now = datetime.now(timezone.utc)
    fixtures = [
        ("alpha", True, "pulse-a", "Alpha Pulse", "ip", now - timedelta(hours=1)),
        ("alpha", True, "pulse-a", "Alpha Pulse", "domain", now - timedelta(hours=2)),
        ("beta", False, "pulse-b", "Beta Pulse", "ip", now - timedelta(hours=3)),
        ("beta", False, "pulse-c", "Gamma Pulse", "domain", now - timedelta(hours=30)),
        ("beta", False, "pulse-c", "Gamma Pulse", "ip", now - timedelta(hours=48)),
    ]

    for source, is_aggregator, pulse_id, pulse_name, indicator_type, fetched_at in fixtures:
        session.add(
            ThreatIntelRef(
                source=source,
                is_aggregator=is_aggregator,
                pulse_id=pulse_id,
                pulse_name=pulse_name,
                pulse_created=fetched_at,
                indicator_type=indicator_type,
                indicator_value=f"{source}-{indicator_type}-{pulse_id}",
                fetched_at=fetched_at,
                source_url=f"https://{source}.example/{pulse_id}",
            )
        )
    session.commit()

    local_app = FastAPI()

    @local_app.get("/api/threat-intel/source-coverage")
    def threat_intel_source_coverage(db=Depends(get_session)):
        return compute_threat_intel_coverage(db)

    def override_get_session():
        yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/threat-intel/source-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_indicators"] == 5
        assert len(payload["by_source"]) == 2
        assert payload["aggregator_count"] == 2

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()