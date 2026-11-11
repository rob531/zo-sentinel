from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry


class GapServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    age_hours: Optional[float] = None
    scan_count: Optional[int] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None


class HeartbeatGapResponse(BaseModel):
    generated_at: datetime
    threshold_hours: int
    total_servers: int
    never_seen: int
    stale_count: int
    scan_behind_registry: int
    gap_servers: list[GapServer] = Field(default_factory=list)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(value: Optional[datetime], now: datetime) -> Optional[float]:
    timestamp = _utc(value)
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 3600.0)


def get_heartbeat_gap(
    session: Session = Depends(get_session),
    hours: int = Query(168, ge=0),
) -> HeartbeatGapResponse:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
    ).all()

    never_seen = 0
    stale_count = 0
    scan_behind_registry = 0
    gap_servers: list[GapServer] = []

    for row in rows:
        last_seen = _utc(row.last_seen)
        last_scanned = _utc(row.last_scanned)
        age = _age_hours(row.last_seen, now)

        if last_seen is None:
            never_seen += 1
        elif age is not None and age > hours:
            stale_count += 1

        scan_count = row.scan_count or 0
        if (
            scan_count > 1
            and last_seen is not None
            and last_scanned is not None
            and last_scanned < last_seen
        ):
            scan_behind_registry += 1

        if last_seen is None or (age is not None and age > hours):
            gap_servers.append(
                GapServer(
                    server_id=row.server_id,
                    name=row.name,
                    registry_source=row.registry_source,
                    last_seen=row.last_seen,
                    last_scanned=row.last_scanned,
                    age_hours=age,
                    scan_count=row.scan_count,
                    verdict=row.verdict,
                    risk_tier=row.risk_tier,
                )
            )

    gap_servers.sort(
        key=lambda item: (
            item.age_hours is not None,
            item.age_hours if item.age_hours is not None else -1.0,
            item.server_id,
        ),
        reverse=True,
    )

    return HeartbeatGapResponse(
        generated_at=now,
        threshold_hours=hours,
        total_servers=len(rows),
        never_seen=never_seen,
        stale_count=stale_count,
        scan_behind_registry=scan_behind_registry,
        gap_servers=gap_servers[:50],
    )


def build_service() -> FastAPI:
    service = FastAPI(title="Registry Heartbeat Gap API")

    @service.get(
        "/api/registry/heartbeat-gap",
        response_model=HeartbeatGapResponse,
    )
    def heartbeat_gap(
        hours: int = Query(168, ge=0),
        session: Session = Depends(get_session),
    ) -> HeartbeatGapResponse:
        return get_heartbeat_gap(session=session, hours=hours)

    return service


app = build_service()


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="recent",
                    name="Recent server",
                    registry_source="test",
                    first_seen=now - timedelta(days=2),
                    last_seen=now - timedelta(hours=1),
                    last_scanned=now - timedelta(hours=1),
                    scan_count=1,
                    verdict="healthy",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="stale",
                    name="Stale server",
                    registry_source="test",
                    first_seen=now - timedelta(days=45),
                    last_seen=now - timedelta(days=30),
                    last_scanned=now - timedelta(days=30),
                    scan_count=1,
                    verdict="unknown",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="never-seen",
                    name="Never seen server",
                    registry_source="test",
                    first_seen=None,
                    last_seen=None,
                    last_scanned=None,
                    scan_count=0,
                    verdict="unknown",
                    risk_tier="unknown",
                ),
                McpServerRegistry(
                    server_id="scan-behind",
                    name="Scan behind server",
                    registry_source="test",
                    first_seen=now - timedelta(days=45),
                    last_seen=now - timedelta(days=30),
                    last_scanned=now - timedelta(days=31),
                    scan_count=2,
                    verdict="unknown",
                    risk_tier="medium",
                ),
            ]
        )
        session.commit()

    service = build_service()
    service.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(service) as client:
            response = client.get("/api/registry/heartbeat-gap")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["never_seen"] == 1, payload
            assert payload["stale_count"] >= 1, payload
            ages = [
                item["age_hours"]
                for item in payload["gap_servers"]
                if item["age_hours"] is not None
            ]
            assert ages == sorted(ages, reverse=True), payload
    finally:
        service.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()