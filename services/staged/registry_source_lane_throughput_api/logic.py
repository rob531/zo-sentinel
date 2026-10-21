from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceLane(BaseModel):
    registry_source: str
    first_seen: int
    last_seen: int
    scanned: int
    mean_scan_count: float
    tiers: Dict[str, int] = Field(default_factory=dict)


class RegistrySourceLaneTotals(BaseModel):
    servers: int


class RegistrySourceLaneThroughputResponse(BaseModel):
    days: int
    lanes: List[RegistrySourceLane]
    totals: RegistrySourceLaneTotals


def get_registry_source_lane_throughput(
    db: Session = Depends(get_session),
    days: int = 30,
) -> RegistrySourceLaneThroughputResponse:
    if days < 1:
        raise HTTPException(status_code=422, detail="days must be at least 1")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    lane_rows = db.execute(
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
            func.avg(
                func.coalesce(McpServerRegistry.scan_count, 0)
            ).label("mean_scan_count"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    tier_rows = db.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count().label("server_count"),
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    tiers_by_source: Dict[str, Dict[str, int]] = {}
    for source, tier, count in tier_rows:
        source_name = str(source) if source is not None else ""
        tier_name = str(tier) if tier is not None else "unknown"
        tiers_by_source.setdefault(source_name, {})[tier_name] = int(count)

    lanes = []
    for source, first_seen, last_seen, scanned, mean_scan_count in lane_rows:
        source_name = str(source) if source is not None else ""
        lanes.append(
            RegistrySourceLane(
                registry_source=source_name,
                first_seen=int(first_seen or 0),
                last_seen=int(last_seen or 0),
                scanned=int(scanned or 0),
                mean_scan_count=float(mean_scan_count or 0.0),
                tiers=tiers_by_source.get(source_name, {}),
            )
        )

    total_servers = int(
        db.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar()
        or 0
    )

    return RegistrySourceLaneThroughputResponse(
        days=days,
        lanes=lanes,
        totals=RegistrySourceLaneTotals(servers=total_servers),
    )


compute_registry_source_lane_throughput = get_registry_source_lane_throughput
get_lane_throughput = get_registry_source_lane_throughput
registry_source_lane_throughput = get_registry_source_lane_throughput


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = session_factory()
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-1",
                name="server-1",
                registry_source="github",
                first_seen=now - timedelta(days=1),
                last_seen=now,
                last_scanned=now,
                scan_count=4,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="server-2",
                name="server-2",
                registry_source="github",
                first_seen=now - timedelta(days=2),
                last_seen=now,
                last_scanned=now,
                scan_count=6,
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="server-3",
                name="server-3",
                registry_source="gitlab",
                first_seen=now - timedelta(days=3),
                last_seen=now,
                last_scanned=now,
                scan_count=2,
                risk_tier="high",
            ),
        ]
    )
    db.commit()

    that_app = FastAPI()

    @that_app.get(
        "/api/registry/source-lanes",
        response_model=RegistrySourceLaneThroughputResponse,
    )
    def source_lanes(days: int = 30, session: Session = Depends(get_session)):
        return get_registry_source_lane_throughput(db=session, days=days)

    that_app.dependency_overrides[get_session] = lambda: db

    response = TestClient(that_app).get(
        "/api/registry/source-lanes", params={"days": 30}
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["lanes"]) == 2
    assert any(lane["first_seen"] == 2 for lane in payload["lanes"])
    assert payload["totals"]["servers"] == 3
    print("PASS")