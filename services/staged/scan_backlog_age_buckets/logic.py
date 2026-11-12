"""logic.py -- backlog-age computation and API for scan staleness."""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class BacklogAgeBucket(BaseModel):
    label: str
    max_age_days: int
    count: int


class StaleServer(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    days_since_scan: int


class StaleBeyondMax(BaseModel):
    count: int
    servers: List[StaleServer]


class BacklogAgeResponse(BaseModel):
    generated_at: datetime
    registry_source: Optional[str]
    total_servers: int
    never_scanned: int
    buckets: List[BacklogAgeBucket]
    stale_beyond_max: StaleBeyondMax


router = APIRouter(prefix="/api")


def parse_bucket_bounds(value: str) -> List[int]:
    """Parse and normalize the comma-separated day thresholds."""
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        raise HTTPException(status_code=422, detail="buckets must contain day values")

    bounds: List[int] = []
    for part in parts:
        try:
            bound = int(part)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="bucket values must be integers"
            ) from exc
        if bound < 0:
            raise HTTPException(
                status_code=422, detail="bucket values must be non-negative"
            )
        bounds.append(bound)

    return sorted(set(bounds))


def _utc_timestamp(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    else:
        raise TypeError("last_scanned must be a date or datetime")

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def compute_backlog_age(
    db: Session,
    bucket_bounds: List[int],
    registry_source: Optional[str] = None,
) -> BacklogAgeResponse:
    """Compute scan-age buckets from the authoritative registry table."""
    if not bucket_bounds:
        raise ValueError("at least one bucket boundary is required")

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_scanned,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()
    generated_at = datetime.now(timezone.utc)
    maximum_age = max(bucket_bounds)
    counts = {bound: 0 for bound in bucket_bounds}
    never_scanned = 0
    stale: List[Tuple[int, str, Optional[str], Optional[str]]] = []

    for server_id, name, risk_tier, last_scanned in rows:
        scanned_at = _utc_timestamp(last_scanned)
        if scanned_at is None:
            never_scanned += 1
            continue

        age_seconds = (generated_at - scanned_at).total_seconds()
        days_since_scan = max(0, int(age_seconds // 86400))

        if days_since_scan > maximum_age:
            stale.append(
                (days_since_scan, str(server_id), name, risk_tier)
            )
            continue

        index = bisect_right(bucket_bounds, days_since_scan) - 1
        if index < 0:
            index = 0
        counts[bucket_bounds[index]] += 1

    stale.sort(key=lambda item: (-item[0], item[1]))
    buckets = [
        BacklogAgeBucket(
            label=f"{bound}d",
            max_age_days=bound,
            count=int(counts[bound]),
        )
        for bound in bucket_bounds
    ]

    return BacklogAgeResponse(
        generated_at=generated_at,
        registry_source=registry_source,
        total_servers=int(len(rows)),
        never_scanned=int(never_scanned),
        buckets=buckets,
        stale_beyond_max=StaleBeyondMax(
            count=int(len(stale)),
            servers=[
                StaleServer(
                    server_id=server_id,
                    name=name,
                    risk_tier=risk_tier,
                    days_since_scan=days_since_scan,
                )
                for days_since_scan, server_id, name, risk_tier in stale[:20]
            ],
        ),
    )


@router.get("/scans/backlog-age", response_model=BacklogAgeResponse)
def get_backlog_age(
    buckets: str = Query("1,7,30,90"),
    registry_source: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> BacklogAgeResponse:
    """Return the current MCP registry backlog grouped by scan staleness."""
    bounds = parse_bucket_bounds(buckets)
    return compute_backlog_age(db, bounds, registry_source)


def create_app():
    """Create a standalone application exposing this service."""
    from fastapi import FastAPI

    application = FastAPI(title="Scan Backlog Age Buckets")
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    from datetime import timedelta
    from typing import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    seeded_at = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-never",
                    name="Never scanned",
                    registry_source="contract",
                    risk_tier="low",
                    last_scanned=None,
                ),
                McpServerRegistry(
                    server_id="contract-2",
                    name="Two days old",
                    registry_source="contract",
                    risk_tier="medium",
                    last_scanned=seeded_at - timedelta(days=2),
                ),
                McpServerRegistry(
                    server_id="contract-60",
                    name="Sixty days old",
                    registry_source="contract",
                    risk_tier="high",
                    last_scanned=seeded_at - timedelta(days=60),
                ),
                McpServerRegistry(
                    server_id="contract-200",
                    name="Two hundred days old",
                    registry_source="contract",
                    risk_tier="critical",
                    last_scanned=seeded_at - timedelta(days=200),
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scans/backlog-age")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["never_scanned"] == 1
        assert payload["total_servers"] == 4
        bucket_30 = next(
            bucket
            for bucket in payload["buckets"]
            if bucket["max_age_days"] == 30
        )
        assert bucket_30["count"] == 1
        assert any(
            server["server_id"] == "contract-200"
            for server in payload["stale_beyond_max"]["servers"]
        )

    print("PASS")