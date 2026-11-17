import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry", tags=["registry"])


class Bucket(BaseModel):
    label: str
    min_hours: int
    max_hours: Optional[int] = None  # None means open‑ended upper bound
    count: int


class VerdictAgeResponse(BaseModel):
    generated_at: datetime.datetime = Field(..., description="ISO8601 timestamp")
    window_hours: int = Field(..., description="Staleness window in hours")
    total_servers: int
    buckets: List[Bucket]
    by_tier: Dict[str, int]
    stale_count: int
    never_assessed_count: int


def _bucket_definitions() -> List[Bucket]:
    """Static bucket definitions used for counting."""
    return [
        Bucket(label="0-24h", min_hours=0, max_hours=24, count=0),
        Bucket(label="24-72h", min_hours=24, max_hours=72, count=0),
        Bucket(label="72-168h", min_hours=72, max_hours=168, count=0),
        Bucket(label="168h+", min_hours=168, max_hours=None, count=0),
    ]


@router.get(
    "/verdict-age",
    response_model=VerdictAgeResponse,
    summary="Report how stale each MCP server verdict is",
)
def get_verdict_age(
    hours: int = Query(24, ge=0, description="Staleness window in hours"),
    tier: Optional[str] = Query(
        None, description="Optional risk tier filter (exact match)"
    ),
    db: Session = Depends(get_session),
) -> VerdictAgeResponse:
    """Return verdict‑age statistics for MCP server registry entries."""
    now = datetime.datetime.utcnow()

    query = db.query(McpServerRegistry)
    if tier is not None:
        query = query.filter(McpServerRegistry.risk_tier == tier)

    rows = query.all()

    total_servers = len(rows)
    buckets = _bucket_definitions()
    by_tier: Dict[str, int] = {}
    stale_count = 0
    never_assessed_count = 0

    for row in rows:
        # tier aggregation
        tier_key = row.risk_tier or "unknown"
        by_tier[tier_key] = by_tier.get(tier_key, 0) + 1

        # age calculation
        if row.last_assessed is None:
            never_assessed_count += 1
            continue

        age_seconds = (now - row.last_assessed).total_seconds()
        age_hours = age_seconds / 3600.0

        # stale determination
        if age_hours > hours:
            stale_count += 1

        # bucket placement
        for bucket in buckets:
            if bucket.max_hours is None:
                if age_hours >= bucket.min_hours:
                    bucket.count += 1
                    break
            elif bucket.min_hours <= age_hours < bucket.max_hours:
                bucket.count += 1
                break

    response = VerdictAgeResponse(
        generated_at=now,
        window_hours=hours,
        total_servers=total_servers,
        buckets=buckets,
        by_tier=by_tier,
        stale_count=stale_count,
        never_assessed_count=never_assessed_count,
    )
    return response


# --------------------------------------------------------------------------- #
# Self‑test (executed via `python -m services.staged.registry_verdict_age_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import the Base metadata from the app (it is defined in app.db)
    from app.db import Base  # type: ignore

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed test data
    now = datetime.datetime.utcnow()
    test_rows = [
        McpServerRegistry(
            server_id="srv-1",
            risk_tier="high",
            verdict="allow",
            first_seen=now - datetime.timedelta(days=3),
            last_seen=now - datetime.timedelta(days=1),
            last_scanned=now - datetime.timedelta(days=1),
            last_assessed=now - datetime.timedelta(hours=2),
        ),
        McpServerRegistry(
            server_id="srv-2",
            risk_tier="medium",
            verdict="allow",
            first_seen=now - datetime.timedelta(days=5),
            last_seen=now - datetime.timedelta(days=2),
            last_scanned=now - datetime.timedelta(days=2),
            last_assessed=now - datetime.timedelta(hours=100),
        ),
        McpServerRegistry(
            server_id="srv-3",
            risk_tier="low",
            verdict="allow",
            first_seen=now - datetime.timedelta(days=1),
            last_seen=now - datetime.timedelta(hours=12),
            last_scanned=now - datetime.timedelta(hours=12),
            last_assessed=None,
        ),
    ]

    db = SessionLocal()
    db.add_all(test_rows)
    db.commit()

    # Build a FastAPI app that uses the overridden session
    app = FastAPI()
    app.include_router(router)

    def _override_get_session() -> Session:
        return db

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    resp = client.get("/api/registry/verdict-age")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()

    # Assertions per contract
    assert data["total_servers"] == 3, "total_servers mismatch"
    # Find bucket counts by label
    bucket_counts = {b["label"]: b["count"] for b in data["buckets"]}
    assert bucket_counts.get("0-24h", 0) == 1, "0-24h bucket count mismatch"
    assert bucket_counts.get("72-168h", 0) == 1, "72-168h bucket count mismatch"
    assert data["never_assessed_count"] == 1, "never_assessed_count mismatch"
    assert sum(data["by_tier"].values()) == data["total_servers"], "by_tier sum mismatch"

    print("PASS")
    sys.exit(0)