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

try:
    from .logic import get_registry_scan_lane_throughput as _logic_get_registry_scan_lane_throughput
except ImportError:
    _logic_get_registry_scan_lane_throughput = None


class RegistryScanLane(BaseModel):
    registry_source: str
    server_count: int
    scanned_count: int
    never_scanned_count: int
    median_scan_count: float
    oldest_first_seen: Optional[datetime]
    newest_last_seen: Optional[datetime]
    stale_over_7d_count: int


class RegistryScanLaneTotals(BaseModel):
    servers: int
    lanes: int
    never_scanned: int


class RegistryScanLaneThroughputResponse(BaseModel):
    days: int
    generated_at: datetime
    lanes: List[RegistryScanLane]
    totals: RegistryScanLaneTotals


router = APIRouter(prefix="/api", tags=["registry-scan-lanes"])


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _compute_registry_scan_lane_throughput(
    session: Session,
    days: int,
    source: Optional[str],
) -> Dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    lookback_cutoff = generated_at - timedelta(days=days)
    stale_cutoff = generated_at - timedelta(days=7)

    statement = select(McpServerRegistry)
    if source is not None:
        statement = statement.where(McpServerRegistry.registry_source == source)

    rows = list(session.execute(statement).scalars().all())
    grouped: Dict[str, List[McpServerRegistry]] = {}

    for row in rows:
        last_seen = _as_utc(row.last_seen)
        if last_seen is not None and last_seen < lookback_cutoff:
            continue

        lane_source = row.registry_source or "unknown"
        grouped.setdefault(lane_source, []).append(row)

    lanes: List[RegistryScanLane] = []
    total_servers = 0
    total_never_scanned = 0

    for lane_source in sorted(grouped):
        lane_rows = grouped[lane_source]
        scan_counts = [
            max(0, int(row.scan_count or 0))
            for row in lane_rows
        ]
        scanned_count = sum(1 for count in scan_counts if count > 0)
        never_scanned_count = sum(1 for count in scan_counts if count == 0)

        first_seen_values = [
            value
            for value in (_as_utc(row.first_seen) for row in lane_rows)
            if value is not None
        ]
        last_seen_values = [
            value
            for value in (_as_utc(row.last_seen) for row in lane_rows)
            if value is not None
        ]

        stale_over_7d_count = 0
        for row in lane_rows:
            if int(row.scan_count or 0) <= 0:
                continue
            last_scanned = _as_utc(row.last_scanned)
            if last_scanned is not None and last_scanned < stale_cutoff:
                stale_over_7d_count += 1

        lanes.append(
            RegistryScanLane(
                registry_source=lane_source,
                server_count=len(lane_rows),
                scanned_count=scanned_count,
                never_scanned_count=never_scanned_count,
                median_scan_count=float(median(scan_counts)) if scan_counts else 0.0,
                oldest_first_seen=min(first_seen_values) if first_seen_values else None,
                newest_last_seen=max(last_seen_values) if last_seen_values else None,
                stale_over_7d_count=stale_over_7d_count,
            )
        )
        total_servers += len(lane_rows)
        total_never_scanned += never_scanned_count

    return {
        "days": days,
        "generated_at": generated_at,
        "lanes": lanes,
        "totals": {
            "servers": total_servers,
            "lanes": len(lanes),
            "never_scanned": total_never_scanned,
        },
    }


@router.get(
    "/registry/scan-lanes",
    response_model=RegistryScanLaneThroughputResponse,
)
def registry_scan_lanes(
    days: int = Query(default=30, ge=1),
    source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _compute_registry_scan_lane_throughput(session, days, source)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    session = testing_session()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="lane-a-1",
                    name="Lane A 1",
                    registry_source="lane-a",
                    first_seen=now - timedelta(days=20),
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                    verdict="unknown",
                    risk_tier="unrated",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="lane-a-2",
                    name="Lane A 2",
                    registry_source="lane-a",
                    first_seen=now - timedelta(days=15),
                    last_seen=now,
                    last_scanned=now - timedelta(days=10),
                    scan_count=2,
                    verdict="safe",
                    risk_tier="low",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="lane-b-1",
                    name="Lane B 1",
                    registry_source="lane-b",
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_scanned=now - timedelta(days=1),
                    scan_count=1,
                    verdict="safe",
                    risk_tier="low",
                    last_assessed=now,
                ),
                McpServerRegistry(
                    server_id="lane-b-2",
                    name="Lane B 2",
                    registry_source="lane-b",
                    first_seen=now - timedelta(days=5),
                    last_seen=now,
                    last_scanned=now - timedelta(days=2),
                    scan_count=1,
                    verdict="safe",
                    risk_tier="low",
                    last_assessed=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/registry/scan-lanes")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["lanes"]) == 2
            lanes = {lane["registry_source"]: lane for lane in payload["lanes"]}
            assert lanes["lane-a"]["never_scanned_count"] == 1
            assert lanes["lane-a"]["stale_over_7d_count"] >= 1
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()