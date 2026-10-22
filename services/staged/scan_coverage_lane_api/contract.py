from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, insert

from app.db import get_session
from app.models import McpServerRegistry


class CoverageLane(BaseModel):
    registry_source: Optional[str]
    total_servers: int
    scanned_servers: int
    never_scanned: int
    stale_over_24h: int
    avg_scan_count: float
    coverage_pct: float


class CoverageResponse(BaseModel):
    generated_at: str
    total_servers: int
    lanes: list[CoverageLane]


router = APIRouter(prefix="/api")


@router.get("/scan/coverage", response_model=CoverageResponse)
def get_scan_coverage(
    registry_source: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1),
    session: Any = Depends(get_session),
) -> CoverageResponse:
    now = datetime.utcnow()
    stale_before = now - timedelta(days=days)

    scanned = case((McpServerRegistry.last_scanned.is_not(None), 1), else_=0)
    never_scanned = case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)
    stale = case((McpServerRegistry.last_scanned < stale_before, 1), else_=0)
    normalized_scan_count = case(
        (McpServerRegistry.scan_count.is_(None), 0),
        (McpServerRegistry.scan_count < 0, 0),
        else_=McpServerRegistry.scan_count,
    )

    query = session.query(
        McpServerRegistry.registry_source.label("registry_source"),
        func.count(McpServerRegistry.server_id).label("total_servers"),
        func.sum(scanned).label("scanned_servers"),
        func.sum(never_scanned).label("never_scanned"),
        func.sum(stale).label("stale_over_24h"),
        func.avg(normalized_scan_count).label("avg_scan_count"),
    )

    if registry_source is not None:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    rows = (
        query.group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
        .all()
    )

    lanes = []
    for row in rows:
        lane_total = int(row.total_servers or 0)
        lane_scanned = int(row.scanned_servers or 0)
        lanes.append(
            CoverageLane(
                registry_source=row.registry_source,
                total_servers=lane_total,
                scanned_servers=lane_scanned,
                never_scanned=int(row.never_scanned or 0),
                stale_over_24h=int(row.stale_over_24h or 0),
                avg_scan_count=float(row.avg_scan_count or 0.0),
                coverage_pct=(
                    round(lane_scanned * 100.0 / lane_total, 2)
                    if lane_total
                    else 0.0
                ),
            )
        )

    return CoverageResponse(
        generated_at=now.isoformat() + "Z",
        total_servers=sum(lane.total_servers for lane in lanes),
        lanes=lanes,
    )


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


def run() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.execute(
            insert(McpServerRegistry).values(
                [
                    {
                        "server_id": "server-a-1",
                        "registry_source": "alpha",
                        "last_scanned": now - timedelta(hours=1),
                        "scan_count": 3,
                        "last_seen": now,
                    },
                    {
                        "server_id": "server-a-2",
                        "registry_source": "alpha",
                        "last_scanned": now - timedelta(days=2),
                        "scan_count": 1,
                        "last_seen": now,
                    },
                    {
                        "server_id": "server-b-1",
                        "registry_source": "beta",
                        "last_scanned": None,
                        "scan_count": 0,
                        "last_seen": now,
                    },
                    {
                        "server_id": "server-b-2",
                        "registry_source": "beta",
                        "last_scanned": None,
                        "scan_count": -1,
                        "last_seen": now,
                    },
                ]
            )
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scan/coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["lanes"]) == 2, payload
        assert payload["total_servers"] == 4, payload

        never_scanned_lane = next(
            lane for lane in payload["lanes"] if lane["registry_source"] == "beta"
        )
        assert never_scanned_lane["never_scanned"] == 2, payload
        assert never_scanned_lane["coverage_pct"] == 0.0, payload

    print("PASS")


if __name__ == "__main__":
    run()