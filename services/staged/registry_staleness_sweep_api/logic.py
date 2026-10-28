from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_
from sqlalchemy.sql import column

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class RegistryStalenessBucket(BaseModel):
    risk_tier: Optional[str]
    registry_source: Optional[str]
    stale_count: int
    never_seen_count: int
    never_rescanned_count: int


class RegistryStalenessTotals(BaseModel):
    servers: int
    stale_servers: int
    never_seen: int
    never_rescanned: int
    distinct_registry_sources: int


class RegistryStalenessResponse(BaseModel):
    days: int
    generated_at: datetime
    buckets: List[RegistryStalenessBucket]
    totals: RegistryStalenessTotals


@router.get("/registry/staleness", response_model=RegistryStalenessResponse)
def get_registry_staleness(
    days: int = Query(..., ge=0),
    session=Depends(get_session),
) -> RegistryStalenessResponse:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - __import__("datetime").timedelta(days=days)

    stale_seen = or_(
        McpServerRegistry.last_seen.is_(None),
        McpServerRegistry.last_seen < cutoff,
    )
    stale_scanned = or_(
        McpServerRegistry.last_scanned.is_(None),
        McpServerRegistry.last_scanned < cutoff,
    )
    stale = or_(stale_seen, stale_scanned)

    bucket_rows = (
        session.query(
            McpServerRegistry.risk_tier.label("risk_tier"),
            McpServerRegistry.registry_source.label("registry_source"),
            func.count(McpServerRegistry.server_id).label("stale_count"),
            func.sum(
                case((McpServerRegistry.last_seen.is_(None), 1), else_=0)
            ).label("never_seen_count"),
            func.sum(
                case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)
            ).label("never_rescanned_count"),
        )
        .filter(stale)
        .group_by(
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
        .all()
    )

    server_count, distinct_sources = (
        session.query(
            func.count(McpServerRegistry.server_id),
            func.count(func.distinct(McpServerRegistry.registry_source)),
        ).one()
    )

    buckets = [
        RegistryStalenessBucket(
            risk_tier=risk_tier,
            registry_source=registry_source,
            stale_count=int(stale_count or 0),
            never_seen_count=int(never_seen_count or 0),
            never_rescanned_count=int(never_rescanned_count or 0),
        )
        for risk_tier, registry_source, stale_count, never_seen_count, never_rescanned_count in bucket_rows
    ]

    return RegistryStalenessResponse(
        days=days,
        generated_at=generated_at,
        buckets=buckets,
        totals=RegistryStalenessTotals(
            servers=int(server_count or 0),
            stale_servers=sum(bucket.stale_count for bucket in buckets),
            never_seen=sum(bucket.never_seen_count for bucket in buckets),
            never_rescanned=sum(bucket.never_rescanned_count for bucket in buckets),
            distinct_registry_sources=int(distinct_sources or 0),
        ),
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from datetime import timedelta

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
    test_session = TestSession()
    test_session.add_all(
        [
            McpServerRegistry(
                server_id="fresh",
                name="Fresh",
                registry_source="official",
                risk_tier="low",
                first_seen=now - timedelta(days=60),
                last_seen=now - timedelta(days=1),
                last_scanned=now - timedelta(days=1),
                scan_count=2,
            ),
            McpServerRegistry(
                server_id="old",
                name="Old",
                registry_source="community",
                risk_tier="high",
                first_seen=now - timedelta(days=120),
                last_seen=now - timedelta(days=90),
                last_scanned=now - timedelta(days=90),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="unseen",
                name="Unseen",
                registry_source="official",
                risk_tier="medium",
                first_seen=now - timedelta(days=10),
                last_seen=None,
                last_scanned=now - timedelta(days=1),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="unscanned",
                name="Unscanned",
                registry_source="community",
                risk_tier="medium",
                first_seen=now - timedelta(days=10),
                last_seen=now - timedelta(days=1),
                last_scanned=None,
                scan_count=0,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/staleness", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 4
        assert payload["totals"]["never_seen"] == 1
        assert payload["totals"]["never_rescanned"] == 1
        assert all(
            "risk_tier" in bucket and "registry_source" in bucket
            for bucket in payload["buckets"]
        )

    print("PASS")