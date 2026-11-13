"""logic.py -- the service's data/computation layer for scan backlog age buckets."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


DEFAULT_BUCKETS = "1,7,30,90"


router = APIRouter(prefix="/api")
app = FastAPI(title="Scan Backlog Age Buckets")
app.include_router(router)


def _parse_bucket_bounds(value: str) -> List[int]:
    """Parse and normalize the requested age thresholds."""
    bounds: List[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            bound = int(item)
        except ValueError:
            continue
        if bound >= 0 and bound not in bounds:
            bounds.append(bound)
    return sorted(bounds)


def _days_since_scan(value: datetime, now: datetime) -> int:
    """Return the whole number of elapsed days since a scan."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (now - value).total_seconds()
    return max(0, int(seconds // 86400))


def backlog_age_buckets(
    db: Session,
    buckets: str = DEFAULT_BUCKETS,
    registry_source: Optional[str] = None,
) -> Dict[str, object]:
    """Compute scan-age buckets from the real server registry."""
    now = datetime.now(timezone.utc)
    bounds = _parse_bucket_bounds(buckets)

    query = select(McpServerRegistry)
    if registry_source is not None:
        query = query.where(McpServerRegistry.registry_source == registry_source)

    servers = list(db.execute(query).scalars().all())
    never_scanned = 0
    ages: List[Tuple[datetime, int]] = []

    for server in servers:
        if server.last_scanned is None:
            never_scanned += 1
            continue
        ages.append((server.last_scanned, _days_since_scan(server.last_scanned, now)))

    bucket_output = [
        {
            "label": f"{bound}d+",
            "max_age_days": int(bound),
            "count": int(sum(1 for _, age in ages if age >= bound)),
        }
        for bound in bounds
    ]

    maximum = bounds[-1] if bounds else None
    stale_servers = [
        {
            "server_id": str(server.server_id),
            "name": server.name,
            "risk_tier": server.risk_tier,
            "days_since_scan": int(age),
        }
        for server, age in sorted(
            (
                (server, _days_since_scan(server.last_scanned, now))
                for server in servers
                if server.last_scanned is not None
                and (maximum is None or _days_since_scan(server.last_scanned, now) > maximum)
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:20]
    ]

    return {
        "generated_at": now.isoformat(),
        "registry_source": registry_source,
        "total_servers": int(len(servers)),
        "never_scanned": int(never_scanned),
        "buckets": bucket_output,
        "stale_beyond_max": {
            "count": int(len(stale_servers)),
            "servers": stale_servers,
        },
    }


@router.get("/scans/backlog-age")
def get_backlog_age_buckets(
    buckets: str = Query(DEFAULT_BUCKETS),
    registry_source: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Return the scan backlog age distribution."""
    return backlog_age_buckets(db, buckets=buckets, registry_source=registry_source)


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_db = TestSession()
    base_time = datetime.now(timezone.utc)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="never",
                name="Never scanned",
                registry_source="test",
                risk_tier="unknown",
            ),
            McpServerRegistry(
                server_id="two-days",
                name="Two days old",
                registry_source="test",
                risk_tier="low",
                last_scanned=base_time - timedelta(days=2),
            ),
            McpServerRegistry(
                server_id="sixty-days",
                name="Sixty days old",
                registry_source="test",
                risk_tier="medium",
                last_scanned=base_time - timedelta(days=60),
            ),
            McpServerRegistry(
                server_id="two-hundred-days",
                name="Two hundred days old",
                registry_source="test",
                risk_tier="high",
                last_scanned=base_time - timedelta(days=200),
            ),
        ]
    )
    test_db.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        yield test_db

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scans/backlog-age")
        assert response.status_code == 200
        payload = response.json()
        assert payload["never_scanned"] == 1
        assert payload["total_servers"] == 4
        assert any(
            bucket["max_age_days"] == 30 and bucket["count"] >= 1
            for bucket in payload["buckets"]
        )
        assert any(
            server["server_id"] == "two-hundred-days"
            for server in payload["stale_beyond_max"]["servers"]
        )

    test_db.close()
    test_engine.dispose()
    print("PASS")