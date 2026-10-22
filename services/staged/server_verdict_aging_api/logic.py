from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

BAND_FRESH = "fresh"
BAND_AGING = "aging"
BAND_STALE = "stale"
BAND_OVERDUE = "overdue"

BAND_ORDER = {
    BAND_FRESH: 0,
    BAND_AGING: 1,
    BAND_STALE: 2,
    BAND_OVERDUE: 3,
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_in_days(value: datetime | None, now: datetime) -> int:
    assessed = _as_utc(value)
    if assessed is None:
        return 30
    return max(0, (now - assessed).days)


def _band_for_age(age_days: int) -> str:
    if age_days <= 1:
        return BAND_FRESH
    if age_days <= 7:
        return BAND_AGING
    if age_days < 30:
        return BAND_STALE
    return BAND_OVERDUE


def get_verdict_aging(
    days: int = 30,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be greater than zero")

    now = datetime.now(timezone.utc)
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.confidence,
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
        McpServerRegistry.scan_count,
    )
    rows = db.execute(statement).all()

    groups: dict[tuple[str, int, str, str], list[float]] = {}
    total_servers = 0
    overdue_servers = 0

    for row in rows:
        band = _band_for_age(_age_in_days(row.last_assessed, now))
        if band == BAND_OVERDUE:
            overdue_servers += 1

        total_servers += 1
        verdict = row.verdict
        risk_tier = row.risk_tier
        key = (band, verdict or "", risk_tier or "", str(row.server_id))
        group_key = (band, verdict, risk_tier)
        groups.setdefault(group_key, [])

        if row.confidence is not None:
            confidence = float(row.confidence)
            groups[group_key].append(confidence)
        else:
            groups[group_key] = groups[group_key]

    band_counts: dict[tuple[str, str | None, str | None], int] = {}
    for row in rows:
        band = _band_for_age(_age_in_days(row.last_assessed, now))
        group_key = (band, row.verdict, row.risk_tier)
        band_counts[group_key] = band_counts.get(group_key, 0) + 1

    bands: list[dict[str, Any]] = []
    for (band, verdict, risk_tier), count in band_counts.items():
        confidences = groups.get((band, verdict, risk_tier), [])
        avg_confidence = (
            round(sum(confidences) / len(confidences), 4) if confidences else None
        )
        bands.append(
            {
                "band": band,
                "verdict": verdict,
                "risk_tier": risk_tier,
                "count": count,
                "avg_confidence": avg_confidence,
            }
        )

    bands.sort(
        key=lambda item: (
            BAND_ORDER[item["band"]],
            item["verdict"] or "",
            item["risk_tier"] or "",
        )
    )

    return {
        "days": days,
        "bands": bands,
        "totals": {
            "servers": total_servers,
            "overdue": overdue_servers,
        },
    }


def get_verdict_aging_data(
    days: int = 30,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_verdict_aging(days=days, db=db)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seeded = [
        ("fresh-server", "fresh", "low", 0.90, now - timedelta_days(0)),
        ("aging-server", "aging", "medium", 0.70, now - timedelta_days(3)),
        ("stale-server", "stale", "high", 0.50, now - timedelta_days(10)),
        ("overdue-server", "stale", "critical", 0.30, now - timedelta_days(40)),
    ]

    with TestingSession() as session:
        for server_id, name, risk_tier, confidence, last_assessed in seeded:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    verdict=name,
                    risk_tier=risk_tier,
                    confidence=confidence,
                    last_assessed=last_assessed,
                    last_seen=last_assessed,
                    scan_count=1,
                )
            )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    application = FastAPI()
    application.dependency_overrides[get_session] = override_get_session

    @application.get("/api/servers/verdict-aging")
    def verdict_aging(
        days: int = Query(30, ge=1),
        db: Session = Depends(get_session),
    ):
        return get_verdict_aging(days=days, db=db)

    with TestClient(application) as client:
        response = client.get("/api/servers/verdict-aging", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert payload["totals"]["servers"] == 4
        assert payload["totals"]["overdue"] == 1

        by_band = {item["band"]: item for item in payload["bands"]}
        assert set(by_band) == {BAND_FRESH, BAND_AGING, BAND_STALE, BAND_OVERDUE}
        assert by_band[BAND_FRESH]["count"] == 1
        assert by_band[BAND_AGING]["count"] == 1
        assert by_band[BAND_STALE]["count"] == 1
        assert by_band[BAND_OVERDUE]["count"] == 1
        assert by_band[BAND_FRESH]["avg_confidence"] == 0.9
        assert by_band[BAND_AGING]["avg_confidence"] == 0.7
        assert by_band[BAND_STALE]["avg_confidence"] == 0.5
        assert by_band[BAND_OVERDUE]["avg_confidence"] == 0.3

    print("PASS")


def timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


if __name__ == "__main__":
    _self_test()