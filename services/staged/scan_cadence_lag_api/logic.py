from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpServerRegistry


class ScanCadenceLagSource(BaseModel):
    registry_source: str
    server_count: int
    never_scanned: int
    median_hours_since_scan: float | None = None
    max_hours_since_scan: float | None = None
    pct_stale_over_24h: float


class ScanCadenceLagResponse(BaseModel):
    generated_at: str
    sources: list[ScanCadenceLagSource]
    total_servers: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hours_since(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    delta = now - _as_utc(value)
    return max(0.0, delta.total_seconds() / 3600.0)


def _percentile_median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def get_scan_cadence_lag(
    source: str | None = None,
    session: Any = Depends(get_session),
    now: datetime | None = None,
) -> dict[str, Any]:
    query = session.query(McpServerRegistry)
    if source is not None:
        query = query.filter(McpServerRegistry.registry_source == source)
    rows = query.all()

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.registry_source, []).append(row)

    sources: list[dict[str, Any]] = []
    for registry_source, source_rows in sorted(grouped.items()):
        never_scanned = 0
        ages: list[float] = []
        for row in source_rows:
            age = _hours_since(row.last_scanned, reference)
            if age is None:
                never_scanned += 1
            else:
                ages.append(age)

        scanned_count = len(ages)
        stale_count = sum(1 for age in ages if age > 24.0)
        sources.append(
            {
                "registry_source": registry_source,
                "server_count": len(source_rows),
                "never_scanned": never_scanned,
                "median_hours_since_scan": _percentile_median(ages),
                "max_hours_since_scan": max(ages) if ages else None,
                "pct_stale_over_24h": (
                    100.0 * stale_count / scanned_count if scanned_count else 0.0
                ),
            }
        )

    if source is not None and not any(
        item["registry_source"] == source for item in sources
    ):
        sources.append(
            {
                "registry_source": source,
                "server_count": 0,
                "never_scanned": 0,
                "median_hours_since_scan": None,
                "max_hours_since_scan": None,
                "pct_stale_over_24h": 0.0,
            }
        )

    return {
        "generated_at": reference.isoformat(),
        "sources": sources,
        "total_servers": len(rows),
    }


def get_scan_cadence_lag_for_source(
    source: str,
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    return get_scan_cadence_lag(source=source, session=session)


router = APIRouter(prefix="/api")


@router.get(
    "/registry/scan-cadence-lag",
    response_model=ScanCadenceLagResponse,
)
def scan_cadence_lag(
    source: str,
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    return get_scan_cadence_lag(source=source, session=session)


def run_self_test() -> None:
    from datetime import timedelta

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
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc)
    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="self-test-0",
                registry_source="self-test",
                scan_count=1,
                last_scanned=now - timedelta(hours=0),
            ),
            McpServerRegistry(
                server_id="self-test-48",
                registry_source="self-test",
                scan_count=1,
                last_scanned=now - timedelta(hours=48),
            ),
            McpServerRegistry(
                server_id="self-test-null",
                registry_source="self-test",
                scan_count=0,
                last_scanned=None,
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        try:
            yield testing_session
        finally:
            testing_session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/scan-cadence-lag",
            params={"source": "self-test"},
        )

    assert response.status_code == 200
    payload = response.json()
    source_row = next(
        item for item in payload["sources"] if item["registry_source"] == "self-test"
    )
    assert source_row["never_scanned"] == 1
    assert source_row["server_count"] == 3
    assert source_row["max_hours_since_scan"] >= 48
    print("PASS")


if __name__ == "__main__":
    run_self_test()