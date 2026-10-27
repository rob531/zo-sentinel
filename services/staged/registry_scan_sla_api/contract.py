from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | None) -> float:
    value = _utc(value)
    if value is None:
        return math.inf
    return value.timestamp()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def calculate_scan_sla(session: Session, days: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    rows = list(session.execute(select(McpServerRegistry)).scalars().all())

    grouped: dict[str, list[McpServerRegistry]] = {}
    for row in rows:
        grouped.setdefault(row.registry_source, []).append(row)

    sources: list[dict[str, Any]] = []
    total_never_scanned = 0

    for registry_source in sorted(grouped):
        source_rows = grouped[registry_source]
        never_scanned_rows = [row for row in source_rows if row.last_scanned is None]
        stale_rows = [
            row
            for row in source_rows
            if row.last_scanned is not None and _utc(row.last_scanned) < cutoff
        ]
        scan_ages = [
            max(
                0.0,
                (now - (_utc(row.last_scanned) or now)).total_seconds() / 3600.0,
            )
            for row in source_rows
            if row.last_scanned is not None
        ]

        oldest_unscanned = sorted(
            never_scanned_rows,
            key=lambda row: _timestamp(row.first_seen),
        )[:10]

        never_scanned_count = len(never_scanned_rows)
        total_never_scanned += never_scanned_count

        sources.append(
            {
                "registry_source": registry_source,
                "total": len(source_rows),
                "never_scanned": never_scanned_count,
                "stale": len(stale_rows),
                "median_scan_age_hours": (
                    float(statistics.median(scan_ages)) if scan_ages else 0.0
                ),
                "p90_scan_age_hours": _percentile(scan_ages, 0.90),
                "oldest_unscanned": [
                    {
                        "server_id": row.server_id,
                        "name": row.name,
                        "first_seen": row.first_seen,
                    }
                    for row in oldest_unscanned
                ],
            }
        )

    return {
        "days": days,
        "sources": sources,
        "totals": {
            "servers": len(rows),
            "never_scanned": total_never_scanned,
        },
    }


@router.get("/registry/scan-sla")
def registry_scan_sla(
    days: int = Query(default=7, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return calculate_scan_sla(session, days)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_scan = now - timedelta(days=40)
    recent_scan = now - timedelta(days=1)

    rows = [
        McpServerRegistry(
            server_id="server-never-scanned",
            name="Never Scanned Server",
            registry_source="source-a",
            first_seen=now - timedelta(days=50),
            last_seen=now,
            last_scanned=None,
            scan_count=0,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="server-stale",
            name="Stale Server",
            registry_source="source-a",
            first_seen=now - timedelta(days=80),
            last_seen=now,
            last_scanned=old_scan,
            scan_count=1,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="server-fresh-a",
            name="Fresh Server A",
            registry_source="source-b",
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=recent_scan,
            scan_count=2,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="server-fresh-b",
            name="Fresh Server B",
            registry_source="source-b",
            first_seen=now - timedelta(days=5),
            last_seen=now,
            last_scanned=recent_scan,
            scan_count=3,
            risk_tier="low",
        ),
    ]

    session = testing_session()
    try:
        session.add_all(rows)
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.get("/api/registry/scan-sla", params={"days": 7})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["never_scanned"] == 1
        source_a = next(
            source
            for source in payload["sources"]
            if source["registry_source"] == "source-a"
        )
        assert source_a["stale"] == 1
        assert source_a["total"] == 2
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()