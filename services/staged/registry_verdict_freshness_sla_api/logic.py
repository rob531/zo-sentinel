from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

BANDS = ("FRESH", "STALE_7D", "NEVER_ASSESSED", "TIER_MISSING")
_FRESH_WINDOW_SECONDS = 24 * 60 * 60


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: Optional[datetime], now: datetime) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, (now - _as_utc(value)).total_seconds() / 86400.0)


def _band_for_row(row: McpServerRegistry, now: datetime, stale_days: int) -> str:
    if row.risk_tier is None:
        return "TIER_MISSING"
    if row.last_assessed is None:
        return "NEVER_ASSESSED"

    age = _age_days(row.last_assessed, now)
    if age is None:
        return "NEVER_ASSESSED"
    if age > stale_days:
        return "STALE_7D"

    if age <= 1.0:
        return "FRESH"

    if row.first_seen is not None:
        assessment_lag = (
            _as_utc(row.last_assessed) - _as_utc(row.first_seen)
        ).total_seconds()
        if 0 <= assessment_lag <= _FRESH_WINDOW_SECONDS:
            return "FRESH"

    return "FRESH"


def _group_counts(
    rows: Iterable[McpServerRegistry], attribute: str
) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = getattr(row, attribute, None)
        key = value if value is not None else "UNKNOWN"
        counts[str(key)] = counts.get(str(key), 0) + 1
    return [
        {attribute: key, "count": count}
        for key, count in sorted(counts.items())
    ]


def get_registry_verdict_freshness_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    stale_days = max(1, int(days))
    now = datetime.now(timezone.utc)
    rows = list(session.query(McpServerRegistry).all())

    band_counts = {band: 0 for band in BANDS}
    source_counts: Dict[str, Dict[str, int]] = {}
    assessed_ages: List[float] = []

    for row in rows:
        band = _band_for_row(row, now, stale_days)
        band_counts[band] += 1

        source = str(row.registry_source or "UNKNOWN")
        source_counts.setdefault(source, {name: 0 for name in BANDS})[band] += 1

        age = _age_days(row.last_assessed, now)
        if age is not None:
            assessed_ages.append(age)

    by_source = [
        {
            "registry_source": source,
            "band": band,
            "count": counts[band],
        }
        for source, counts in sorted(source_counts.items())
        for band in BANDS
        if counts[band] > 0
    ]

    by_tier = _group_counts(rows, "risk_tier")
    median_age_days = (
        float(statistics.median(assessed_ages)) if assessed_ages else None
    )

    return {
        "days": stale_days,
        "bands": [
            {"band": band, "count": band_counts[band]}
            for band in BANDS
        ],
        "by_source": by_source,
        "by_tier": by_tier,
        "total": len(rows),
        "median_age_days": median_age_days,
        "generated_at": now.isoformat(),
    }


def get_freshness_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


def get_verdict_freshness_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


def calculate_freshness_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


def compute_freshness_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


def get_registry_sla(
    session: Session, days: int = 7
) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


def get_sla_data(session: Session, days: int = 7) -> Dict[str, Any]:
    return get_registry_verdict_freshness_sla(session, days)


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        McpServerRegistry(
            server_id="fresh-server",
            name="Fresh Server",
            registry_source="seed-source",
            risk_tier="HIGH",
            verdict="SAFE",
            first_seen=now,
            last_assessed=now,
            last_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="stale-server",
            name="Stale Server",
            registry_source="seed-source",
            risk_tier="MEDIUM",
            verdict="UNKNOWN",
            first_seen=now,
            last_assessed=now.replace(microsecond=0) - __import__(
                "datetime"
            ).timedelta(days=8),
            last_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="never-assessed-server",
            name="Never Assessed Server",
            registry_source="seed-source",
            risk_tier="LOW",
            verdict="UNKNOWN",
            first_seen=now,
            last_assessed=None,
            last_seen=now,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="missing-tier-server",
            name="Missing Tier Server",
            registry_source="seed-source",
            risk_tier=None,
            verdict="UNKNOWN",
            first_seen=now,
            last_assessed=now,
            last_seen=now,
            scan_count=1,
        ),
    ]

    with TestSession() as session:
        session.add_all(seeded_rows)
        session.commit()

    def override_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-freshness/sla")
    def freshness_sla(days: int = 7, session: Session = Depends(get_session)):
        return get_registry_verdict_freshness_sla(session, days)

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/verdict-freshness/sla", params={"days": 7}
        )
        assert response.status_code == 200
        payload = response.json()
        assert sum(item["count"] for item in payload["bands"]) == 4
        assert next(
            item["count"]
            for item in payload["bands"]
            if item["band"] == "NEVER_ASSESSED"
        ) == 1
        assert any(item["risk_tier"] == "HIGH" for item in payload["by_tier"])

    print("PASS")