from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BAND_0_7 = "0-7"
BAND_8_30 = "8-30"
BAND_31_90 = "31-90"
BAND_90_PLUS = "90+"


class RegistryStalenessServer(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    age_days: int
    band: str
    last_assessed: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class RegistryStalenessBand(BaseModel):
    band: str
    count: int


class RegistryStalenessResponse(BaseModel):
    days_threshold: int
    total_stale: int
    by_band: list[RegistryStalenessBand]
    servers: list[RegistryStalenessServer]


def _utc_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def compute_age_days(
    last_assessed: datetime | date | None,
    last_seen: datetime | date | None,
    now: datetime | None = None,
) -> int:
    activity_at = last_assessed if last_assessed is not None else last_seen
    if activity_at is None:
        return 0
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)
    elapsed = reference - _utc_datetime(activity_at)
    return max(0, elapsed.days)


def staleness_band(age_days: int) -> str:
    if age_days <= 7:
        return BAND_0_7
    if age_days <= 30:
        return BAND_8_30
    if age_days <= 90:
        return BAND_31_90
    return BAND_90_PLUS


def get_registry_staleness(
    session: Session,
    days: int = 30,
    limit: int = 100,
    risk_tier: str | None = None,
) -> dict:
    activity_at = func.coalesce(
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
    )

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
        McpServerRegistry.last_assessed,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        activity_at.label("activity_at"),
    ).where(activity_at.is_not(None))

    if risk_tier is not None:
        statement = statement.where(McpServerRegistry.risk_tier == risk_tier)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)
    stale_rows = []

    for row in rows:
        age_days = compute_age_days(row.last_assessed, row.last_seen, now)
        if age_days <= days:
            continue
        stale_rows.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
                "trust_score": row.trust_score,
                "age_days": age_days,
                "band": staleness_band(age_days),
                "last_assessed": row.last_assessed,
                "last_seen": row.last_seen,
            }
        )

    stale_rows.sort(
        key=lambda server: (-server["age_days"], str(server["server_id"]))
    )
    band_counts = {
        BAND_0_7: 0,
        BAND_8_30: 0,
        BAND_31_90: 0,
        BAND_90_PLUS: 0,
    }
    for server in stale_rows:
        band_counts[server["band"]] += 1

    effective_limit = max(0, limit)
    return {
        "days_threshold": days,
        "total_stale": len(stale_rows),
        "by_band": [
            {"band": band, "count": count}
            for band, count in band_counts.items()
        ],
        "servers": stale_rows[:effective_limit],
    }


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
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    risk_tier="low",
                    trust_score=0.95,
                    last_assessed=now - timedelta(days=1),
                    last_seen=now - timedelta(days=1),
                    last_scanned=now - timedelta(days=1),
                    scan_count=10,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    risk_tier="medium",
                    trust_score=0.70,
                    last_assessed=now - timedelta(days=40),
                    last_seen=now - timedelta(days=40),
                    last_scanned=now - timedelta(days=40),
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="very-stale-server",
                    name="Very Stale Server",
                    risk_tier="high",
                    trust_score=0.40,
                    last_assessed=now - timedelta(days=200),
                    last_seen=now - timedelta(days=200),
                    last_scanned=now - timedelta(days=200),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    @that_app.get(
        "/api/registry-staleness",
        response_model=RegistryStalenessResponse,
    )
    def registry_staleness_endpoint(
        days: int = 30,
        limit: int = 100,
        risk_tier: str | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        return get_registry_staleness(
            session=session,
            days=days,
            limit=limit,
            risk_tier=risk_tier,
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry-staleness", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_stale"] == 2
        bands = {entry["band"]: entry["count"] for entry in payload["by_band"]}
        assert bands["90+"] == 1
        assert all(
            server["server_id"] != "fresh-server"
            for server in payload["servers"]
        )
        assert any(
            server["server_id"] == "very-stale-server"
            and server["band"] == "90+"
            for server in payload["servers"]
        )

    test_engine.dispose()
    print("PASS")