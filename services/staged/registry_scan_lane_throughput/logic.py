from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistryScanLane(BaseModel):
    registry_source: str
    server_count: int
    scanned_count: int
    never_scanned_count: int
    median_scan_count: float
    oldest_first_seen: Optional[str]
    newest_last_seen: Optional[str]
    stale_over_7d_count: int


class RegistryScanLanesResponse(BaseModel):
    days: int
    generated_at: str
    lanes: List[RegistryScanLane]
    totals: Dict[str, int]


router = APIRouter(prefix="/api", tags=["registry"])


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    value = _utc(value)
    return value.isoformat() if value is not None else None


def get_registry_scan_lanes(
    days: int = 30,
    source: Optional[str] = None,
    db: Session = Depends(get_session),
) -> RegistryScanLanesResponse:
    """Summarise registry scan activity by registry_source lane."""
    days = max(1, int(days))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    stale_cutoff = now - timedelta(days=7)

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.scan_count,
        )
    ).all()

    lanes: Dict[str, List[Any]] = {}
    for row in rows:
        row_source = row.registry_source
        if row_source is None:
            row_source = ""
        if source is not None and row_source != source:
            continue

        first_seen = _utc(row.first_seen)
        if first_seen is not None and first_seen < cutoff:
            continue

        lanes.setdefault(str(row_source), []).append(row)

    lane_models: List[RegistryScanLane] = []
    for registry_source, lane_rows in sorted(lanes.items()):
        scan_counts = [int(row.scan_count or 0) for row in lane_rows]
        first_seens = [
            _utc(row.first_seen)
            for row in lane_rows
            if row.first_seen is not None
        ]
        last_seens = [
            _utc(row.last_seen)
            for row in lane_rows
            if row.last_seen is not None
        ]

        scanned_count = sum(1 for count in scan_counts if count > 0)
        never_scanned_count = sum(1 for count in scan_counts if count == 0)
        stale_over_7d_count = sum(
            1
            for row in lane_rows
            if row.last_scanned is not None
            and _utc(row.last_scanned) < stale_cutoff
        )

        lane_models.append(
            RegistryScanLane(
                registry_source=registry_source,
                server_count=len(lane_rows),
                scanned_count=scanned_count,
                never_scanned_count=never_scanned_count,
                median_scan_count=float(median(scan_counts)) if scan_counts else 0.0,
                oldest_first_seen=(
                    _iso(min(first_seens)) if first_seens else None
                ),
                newest_last_seen=(
                    _iso(max(last_seens)) if last_seens else None
                ),
                stale_over_7d_count=stale_over_7d_count,
            )
        )

    return RegistryScanLanesResponse(
        days=days,
        generated_at=now.isoformat(),
        lanes=lane_models,
        totals={
            "servers": sum(lane.server_count for lane in lane_models),
            "lanes": len(lane_models),
            "never_scanned": sum(
                lane.never_scanned_count for lane in lane_models
            ),
        },
    )


get_scan_lane_throughput = get_registry_scan_lanes
get_registry_scan_lane_throughput = get_registry_scan_lanes


@router.get(
    "/registry/scan-lanes",
    response_model=RegistryScanLanesResponse,
)
def registry_scan_lanes(
    days: int = Query(default=30, ge=1),
    source: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> RegistryScanLanesResponse:
    return get_registry_scan_lanes(days=days, source=source, db=db)


def _self_test() -> None:
    from fastapi import FastAPI
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
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    old_scan = now - timedelta(days=8)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-a",
                    name="self-test-a",
                    registry_source="alpha",
                    first_seen=now,
                    last_seen=now,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="self-test-b",
                    name="self-test-b",
                    registry_source="alpha",
                    first_seen=now,
                    last_seen=now,
                    last_scanned=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="self-test-c",
                    name="self-test-c",
                    registry_source="beta",
                    first_seen=now,
                    last_seen=now,
                    last_scanned=old_scan,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="self-test-d",
                    name="self-test-d",
                    registry_source="beta",
                    first_seen=now,
                    last_seen=now,
                    last_scanned=now,
                    scan_count=2,
                ),
            ]
        )
        session.commit()

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-lanes")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["lanes"]) == 2
        lanes = {lane["registry_source"]: lane for lane in payload["lanes"]}
        assert lanes["alpha"]["never_scanned_count"] == 1
        assert lanes["beta"]["stale_over_7d_count"] >= 1

    print("PASS")


if __name__ == "__main__":
    _self_test()