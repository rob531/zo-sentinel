from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceBreakdown(BaseModel):
    source: Optional[str]
    total: int
    within_24h: int
    stale_over_7d: int


class RegistrySLAResponse(BaseModel):
    total: int
    verdicted: int
    within_24h: int
    within_24h_pct: float
    stale_over_7d: int
    stale_pct: float
    by_source: List[RegistrySourceBreakdown]


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value = datetime.fromisoformat(text)
    if not isinstance(value, datetime):
        return None
    return _utc_naive(value)


def compute_registry_sla(
    session: Session,
    now: Optional[datetime] = None,
) -> RegistrySLAResponse:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_assessed,
        McpServerRegistry.scan_count,
        McpServerRegistry.registry_source,
    )
    rows = session.execute(statement).all()

    current_time = _utc_naive(now or datetime.now(timezone.utc))
    stale_cutoff = current_time - timedelta(days=7)

    total = len(rows)
    verdicted = 0
    within_24h = 0
    stale_over_7d = 0
    sources: Dict[Optional[str], Dict[str, int]] = {}

    for server_id, verdict, risk_tier, first_seen, last_assessed, scan_count, source in rows:
        source_counts = sources.setdefault(
            source,
            {"total": 0, "within_24h": 0, "stale_over_7d": 0},
        )
        source_counts["total"] += 1

        if verdict is None:
            continue

        verdicted += 1
        first_seen_at = _parse_datetime(first_seen)
        last_assessed_at = _parse_datetime(last_assessed)

        if first_seen_at is not None and last_assessed_at is not None:
            elapsed = last_assessed_at - first_seen_at
            if timedelta(0) <= elapsed <= timedelta(hours=24):
                within_24h += 1
                source_counts["within_24h"] += 1

        if last_assessed_at is not None and last_assessed_at < stale_cutoff:
            stale_over_7d += 1
            source_counts["stale_over_7d"] += 1

    within_24h_pct = round((within_24h / verdicted) * 100, 2) if verdicted else 0.0
    stale_pct = round((stale_over_7d / verdicted) * 100, 2) if verdicted else 0.0

    by_source = [
        RegistrySourceBreakdown(
            source=source,
            total=counts["total"],
            within_24h=counts["within_24h"],
            stale_over_7d=counts["stale_over_7d"],
        )
        for source, counts in sorted(
            sources.items(),
            key=lambda item: (item[0] is None, item[0] or ""),
        )
    ]

    return RegistrySLAResponse(
        total=total,
        verdicted=verdicted,
        within_24h=within_24h,
        within_24h_pct=within_24h_pct,
        stale_over_7d=stale_over_7d,
        stale_pct=stale_pct,
        by_source=by_source,
    )


@router.get("/registry/sla", response_model=RegistrySLAResponse)
def get_registry_sla(session: Session = Depends(get_session)) -> RegistrySLAResponse:
    return compute_registry_sla(session)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    records = [
        McpServerRegistry(
            server_id="fresh-1",
            verdict="trusted",
            risk_tier="low",
            first_seen=now - timedelta(hours=3),
            last_assessed=now - timedelta(hours=2),
            scan_count=1,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="fresh-2",
            verdict="trusted",
            risk_tier="low",
            first_seen=now - timedelta(hours=5),
            last_assessed=now - timedelta(hours=4),
            scan_count=1,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="stale-1",
            verdict="risky",
            risk_tier="high",
            first_seen=now - timedelta(days=12),
            last_assessed=now - timedelta(days=10),
            scan_count=2,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="stale-2",
            verdict="risky",
            risk_tier="high",
            first_seen=now - timedelta(days=15),
            last_assessed=now - timedelta(days=8),
            scan_count=3,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="unverified-1",
            verdict=None,
            risk_tier=None,
            first_seen=now - timedelta(hours=1),
            last_assessed=None,
            scan_count=0,
            registry_source="seed",
        ),
        McpServerRegistry(
            server_id="unverified-2",
            verdict=None,
            risk_tier=None,
            first_seen=now - timedelta(days=2),
            last_assessed=None,
            scan_count=0,
            registry_source="seed",
        ),
    ]

    with test_session_factory() as session:
        session.add_all(records)
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/sla")
        assert response.status_code == 200
        payload = response.json()

    assert payload["total"] == 6
    assert payload["verdicted"] == 4
    assert payload["within_24h"] == 2
    assert payload["stale_over_7d"] == 2
    assert sum(item["total"] for item in payload["by_source"]) == payload["total"]
    print("PASS")


if __name__ == "__main__":
    _self_test()