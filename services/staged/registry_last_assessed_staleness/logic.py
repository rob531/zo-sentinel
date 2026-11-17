"""Logic for registry last-assessed staleness reporting."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_ORDER = ("fresh", "aging", "stale", "never")


def get_last_assessed_staleness(
    db: Session, max_age_hours: float
) -> Dict[str, Any]:
    """Return registry servers grouped by the age of their last assessment."""
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be non-negative")

    now = datetime.utcnow()
    fresh_cutoff = now - timedelta(hours=max_age_hours)
    aging_cutoff = now - timedelta(hours=2 * max_age_hours)

    bucket_expression = case(
        (McpServerRegistry.last_assessed.is_(None), "never"),
        (McpServerRegistry.last_assessed <= fresh_cutoff, "fresh"),
        (McpServerRegistry.last_assessed <= aging_cutoff, "aging"),
        else_="stale",
    )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            bucket_expression.label("bucket"),
        ).order_by(McpServerRegistry.server_id)
    ).all()

    samples: Dict[str, list[str]] = {bucket: [] for bucket in _BUCKET_ORDER}
    counts: Dict[str, int] = {bucket: 0 for bucket in _BUCKET_ORDER}

    for server_id, bucket in rows:
        bucket = str(bucket)
        counts[bucket] += 1
        if len(samples[bucket]) < 20:
            samples[bucket].append(str(server_id))

    return {
        "max_age_hours": max_age_hours,
        "buckets": [
            {
                "bucket": bucket,
                "count": counts[bucket],
                "sample_server_ids": samples[bucket],
            }
            for bucket in _BUCKET_ORDER
        ],
    }


def get_staleness_buckets(
    db: Session, max_age_hours: float
) -> Dict[str, Any]:
    """Compatibility alias for the registry staleness computation."""
    return get_last_assessed_staleness(db, max_age_hours)


def get_registry_last_assessed_staleness(
    db: Session, max_age_hours: float
) -> Dict[str, Any]:
    """Compatibility alias for the registry staleness computation."""
    return get_last_assessed_staleness(db, max_age_hours)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    now = datetime.utcnow()
    seeded_servers = [
        McpServerRegistry(
            server_id="server-fresh-1",
            last_assessed=now - timedelta(hours=0.5),
        ),
        McpServerRegistry(
            server_id="server-fresh-2",
            last_assessed=now - timedelta(hours=1.5),
        ),
        McpServerRegistry(
            server_id="server-aging",
            last_assessed=now - timedelta(hours=3),
        ),
        McpServerRegistry(
            server_id="server-stale",
            last_assessed=now - timedelta(hours=10),
        ),
        McpServerRegistry(
            server_id="server-never",
            last_assessed=None,
        ),
    ]
    test_db.add_all(seeded_servers)
    test_db.commit()

    local_app = FastAPI()

    @local_app.get("/api/registry/last-assessed-staleness")
    def local_endpoint(
        max_age_hours: float = 2,
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_last_assessed_staleness(db, max_age_hours)

    local_app.dependency_overrides[get_session] = lambda: test_db

    with TestClient(local_app) as client:
        response = client.get(
            "/api/registry/last-assessed-staleness",
            params={"max_age_hours": 2},
        )

    assert response.status_code == 200
    payload = response.json()
    assert sum(bucket["count"] for bucket in payload["buckets"]) == len(seeded_servers)
    never = next(
        bucket for bucket in payload["buckets"] if bucket["bucket"] == "never"
    )
    assert never["count"] == 1

    print("PASS")