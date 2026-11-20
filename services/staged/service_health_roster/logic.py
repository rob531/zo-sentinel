"""Data and computation layer for the service health roster."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Mapping, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

DEFAULT_HEARTBEAT_THRESHOLD = 300


class ServiceHealthEntry(BaseModel):
    service: str
    status: str
    age_seconds: int
    state: str


class ServiceHealthCounts(BaseModel):
    healthy: int
    stale: int
    unknown: int


class ServiceHealthRoster(BaseModel):
    services: List[ServiceHealthEntry] = Field(default_factory=list)
    counts: ServiceHealthCounts
    thresholds: Dict[str, int] = Field(default_factory=dict)


def _service_health_table(db: Session) -> Table:
    """Reflect the real service_health table from the bound application DB."""
    bind = db.get_bind()
    if bind is None:
        raise RuntimeError("The application database session has no bound engine")
    return Table("service_health", MetaData(), autoload_with=bind)


def _as_utc_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                return None
    else:
        return None

    if result.tzinfo is None or result.tzinfo.utcoffset(result) is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def build_service_health_roster(
    db: Session,
    thresholds: Optional[Mapping[str, int]] = None,
) -> ServiceHealthRoster:
    """Build the health roster from service_health using per-service thresholds."""
    table = _service_health_table(db)
    rows = db.execute(
        select(table.c.service, table.c.status, table.c.last_heartbeat)
    ).all()

    configured_thresholds = {
        str(name): max(0, int(seconds))
        for name, seconds in (thresholds or {}).items()
    }
    now = datetime.now(timezone.utc)
    entries: List[ServiceHealthEntry] = []
    effective_thresholds: Dict[str, int] = {}

    for row in rows:
        service = str(row.service or "")
        threshold = configured_thresholds.get(
            service, DEFAULT_HEARTBEAT_THRESHOLD
        )
        effective_thresholds[service] = threshold

        heartbeat = _as_utc_datetime(row.last_heartbeat)
        if heartbeat is None:
            age_seconds = -1
            state = "unknown"
        else:
            age_seconds = max(0, int((now - heartbeat).total_seconds()))
            state = "healthy" if age_seconds <= threshold else "stale"

        entries.append(
            ServiceHealthEntry(
                service=service,
                status=str(row.status or "unknown"),
                age_seconds=age_seconds,
                state=state,
            )
        )

    entries.sort(key=lambda entry: entry.service)
    counts = ServiceHealthCounts(
        healthy=sum(entry.state == "healthy" for entry in entries),
        stale=sum(entry.state == "stale" for entry in entries),
        unknown=sum(entry.state == "unknown" for entry in entries),
    )
    return ServiceHealthRoster(
        services=entries,
        counts=counts,
        thresholds=effective_thresholds,
    )


def get_service_health_roster(
    db: Session = Depends(get_session),
) -> ServiceHealthRoster:
    """FastAPI dependency returning the current service health roster."""
    return build_service_health_roster(db)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()
    test_service_health = Table(
        "service_health",
        test_metadata,
        Column("service", String, primary_key=True),
        Column("status", String, nullable=False),
        Column("last_heartbeat", DateTime, nullable=True),
        Column("meta", Text, nullable=True),
    )
    test_metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    test_now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.execute(
            insert(test_service_health),
            [
                {
                    "service": "fresh-daemon",
                    "status": "ok",
                    "last_heartbeat": test_now - timedelta(seconds=10),
                    "meta": None,
                },
                {
                    "service": "stale-daemon",
                    "status": "ok",
                    "last_heartbeat": test_now - timedelta(seconds=600),
                    "meta": None,
                },
                {
                    "service": "missing-daemon",
                    "status": "missing",
                    "last_heartbeat": None,
                    "meta": None,
                },
            ],
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()

    @test_app.get("/api/health/roster", response_model=ServiceHealthRoster)
    def health_roster(
        roster: ServiceHealthRoster = Depends(get_service_health_roster),
    ) -> ServiceHealthRoster:
        return roster

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/health/roster")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["healthy"] == 1
    assert payload["counts"]["stale"] == 1
    assert payload["counts"]["unknown"] == 1
    assert len(payload["services"]) == 3
    print("PASS")