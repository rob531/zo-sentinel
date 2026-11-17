from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


AGE_BANDS = ("never_assessed", "0-7d", "7-30d", "30-90d", "90d+")


class VerdictAgeingResponse(BaseModel):
    days: int
    total_servers: int
    never_assessed: int
    by_verdict: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    by_source: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    stale_total: int


def _group_name(value: str | None) -> str:
    return value if value not in (None, "") else "unknown"


def _age_days(last_assessed: datetime | None, now: datetime) -> int | None:
    if last_assessed is None:
        return None
    if last_assessed.tzinfo is None:
        last_assessed = last_assessed.replace(tzinfo=timezone.utc)
    age = now - last_assessed.astimezone(timezone.utc)
    return max(0, age.days)


def _age_band(age_days: int | None) -> str:
    if age_days is None:
        return "never_assessed"
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "7-30d"
    if age_days <= 90:
        return "30-90d"
    return "90d+"


def _empty_buckets() -> Dict[str, int]:
    return {band: 0 for band in AGE_BANDS}


def calculate_verdict_ageing(session, days: int, now: datetime | None = None) -> VerdictAgeingResponse:
    current_time = now or datetime.now(timezone.utc)
    rows = session.query(McpServerRegistry).all()

    by_verdict: Dict[str, Dict[str, int]] = {}
    by_source: Dict[str, Dict[str, int]] = {}
    never_assessed = 0
    stale_total = 0

    for row in rows:
        verdict = _group_name(row.verdict)
        source = _group_name(row.registry_source)
        age_days = _age_days(row.last_assessed, current_time)
        band = _age_band(age_days)

        by_verdict.setdefault(verdict, _empty_buckets())
        by_source.setdefault(source, _empty_buckets())
        by_verdict[verdict][band] += 1
        by_source[source][band] += 1

        if age_days is None:
            never_assessed += 1
        elif age_days > days:
            stale_total += 1

    return VerdictAgeingResponse(
        days=days,
        total_servers=len(rows),
        never_assessed=never_assessed,
        by_verdict=by_verdict,
        by_source=by_source,
        stale_total=stale_total,
    )


def build_app() -> FastAPI:
    router = APIRouter(prefix="/api")

    @router.get("/registry/verdict-ageing", response_model=VerdictAgeingResponse)
    def registry_verdict_ageing(
        days: int = Query(default=30, ge=0, le=3650),
        session=Depends(get_session),
    ) -> VerdictAgeingResponse:
        return calculate_verdict_ageing(session, days)

    application = FastAPI()
    application.include_router(router)
    return application


def _seed_rows(now: datetime) -> list[McpServerRegistry]:
    return [
        McpServerRegistry(
            server_id="verdict-ageing-fresh-1",
            name="Fresh One",
            registry_source="github",
            verdict="trusted",
            risk_tier="low",
            trust_score=95,
            confidence=0.98,
            last_assessed=now - timedelta(days=1),
            last_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="verdict-ageing-fresh-2",
            name="Fresh Two",
            registry_source="github",
            verdict="trusted",
            risk_tier="low",
            trust_score=92,
            confidence=0.96,
            last_assessed=now - timedelta(days=3),
            last_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="verdict-ageing-fresh-3",
            name="Fresh Three",
            registry_source="npm",
            verdict="restricted",
            risk_tier="medium",
            trust_score=60,
            confidence=0.88,
            last_assessed=now - timedelta(days=6),
            last_seen=now,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="verdict-ageing-never",
            name="Never Assessed",
            registry_source="npm",
            verdict="unknown",
            risk_tier="unknown",
            trust_score=None,
            confidence=None,
            last_assessed=None,
            last_seen=now,
            scan_count=0,
        ),
    ]


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add_all(_seed_rows(now))
        session.commit()

    application = build_app()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(application) as client:
            response = client.get("/api/registry/verdict-ageing", params={"days": 30})
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["days"] == 30
            assert payload["total_servers"] == 4
            assert payload["never_assessed"] == 1
            assert payload["by_verdict"]["trusted"]["0-7d"] == 2
            assert payload["by_source"]["github"]["0-7d"] == 2
            assert payload["stale_total"] == 0
    finally:
        application.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()