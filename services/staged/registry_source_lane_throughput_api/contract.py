from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, insert, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceLaneTierCounts(BaseModel):
    tier: str
    count: int


class SourceLaneThroughput(BaseModel):
    registry_source: str
    first_seen: int
    last_seen: int
    scanned: int
    mean_scan_count: float
    tiers: Dict[str, int]


class SourceLaneThroughputResponse(BaseModel):
    days: int
    lanes: List[SourceLaneThroughput]
    totals: Dict[str, int]


router = APIRouter(prefix="/registry")


@router.get(
    "/source-lanes",
    response_model=SourceLaneThroughputResponse,
)
def get_source_lane_throughput(
    days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> SourceLaneThroughputResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    lane_rows = session.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count(
                case((McpServerRegistry.first_seen >= cutoff, 1), else_=0)
            ).label("first_seen"),
            func.count(
                case((McpServerRegistry.last_seen >= cutoff, 1), else_=0)
            ).label("last_seen"),
            func.count(
                case((McpServerRegistry.last_scanned >= cutoff, 1), else_=0)
            ).label("scanned"),
            func.avg(McpServerRegistry.scan_count).label("mean_scan_count"),
        )
        .where(McpServerRegistry.registry_source.is_not(None))
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    tier_rows = session.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .where(
            McpServerRegistry.registry_source.is_not(None),
            McpServerRegistry.risk_tier.is_not(None),
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .order_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    tiers_by_source: Dict[str, Dict[str, int]] = {}
    for row in tier_rows:
        tiers_by_source.setdefault(row.registry_source, {})[row.risk_tier] = int(
            row.count
        )

    lanes = [
        SourceLaneThroughput(
            registry_source=row.registry_source,
            first_seen=int(row.first_seen),
            last_seen=int(row.last_seen),
            scanned=int(row.scanned),
            mean_scan_count=float(row.mean_scan_count or 0.0),
            tiers=tiers_by_source.get(row.registry_source, {}),
        )
        for row in lane_rows
    ]

    total_servers = session.execute(
        select(
            func.count(
                distinct(McpServerRegistry.server_id)
            )
        )
    ).scalar_one()

    return SourceLaneThroughputResponse(
        days=days,
        lanes=lanes,
        totals={"servers": int(total_servers)},
    )


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "contract-lane-a-1",
            "name": "contract-lane-a-1",
            "url": "https://example.invalid/a1",
            "registry_source": "lane-a",
            "first_seen": now - timedelta(days=2),
            "last_seen": now - timedelta(hours=1),
            "last_scanned": now - timedelta(hours=2),
            "scan_count": 4,
            "risk_tier": "high",
        },
        {
            "server_id": "contract-lane-a-2",
            "name": "contract-lane-a-2",
            "url": "https://example.invalid/a2",
            "registry_source": "lane-a",
            "first_seen": now - timedelta(days=1),
            "last_seen": now - timedelta(hours=3),
            "last_scanned": now - timedelta(hours=4),
            "scan_count": 2,
            "risk_tier": "medium",
        },
        {
            "server_id": "contract-lane-b-1",
            "name": "contract-lane-b-1",
            "url": "https://example.invalid/b1",
            "registry_source": "lane-b",
            "first_seen": now - timedelta(days=3),
            "last_seen": now - timedelta(hours=5),
            "last_scanned": now - timedelta(hours=6),
            "scan_count": 1,
            "risk_tier": "low",
        },
    ]

    with testing_session() as session:
        session.execute(insert(McpServerRegistry), rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/registry/source-lanes",
                params={"days": 30},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["lanes"]) == 2, payload
            assert any(lane["first_seen"] == 2 for lane in payload["lanes"]), payload
            assert payload["totals"]["servers"] == 3, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()