# deps: fastapi, sqlalchemy, pydantic
"""
Perspective Snapshot Rollup API

Aggregates snapshot membership counts per risk tier for a perspective over a
configurable time window. Reads from perspective_snapshots and perspectives
via the standard app DB session dependency.

Public endpoint (no auth required).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_snapshot_rollup_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class TierDistribution(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    none: int = 0


class RollupResponse(BaseModel):
    perspective_id: int
    perspective_name: str
    window_days: int
    snapshot_count: int
    first_at: Optional[str]
    last_at: Optional[str]
    tier_distribution: TierDistribution
    total_members: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_membership(membership_raw) -> list[dict]:
    """Return membership as a list of dicts regardless of input type."""
    if membership_raw is None:
        return []
    if isinstance(membership_raw, list):
        return membership_raw
    if isinstance(membership_raw, str):
        try:
            parsed = json.loads(membership_raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _tier_from_member(member: dict) -> str:
    """Extract risk_tier from a membership dict, defaulting to 'none'."""
    return str(member.get("risk_tier") or member.get("tier") or "none")


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/perspectives/{perspective_id}/snapshots/rollup",
    response_model=RollupResponse,
)
def get_snapshot_rollup(
    perspective_id: int,
    days: int = Query(default=30, ge=1, le=365, description="Rolling window in days"),
    session: Session = Depends(get_session),
) -> RollupResponse:
    """
    Aggregate snapshot membership per tier for a perspective within a time window.

    Returns:
    - perspective_id / perspective_name
    - window_days (echoed from request)
    - snapshot_count: number of snapshots taken in the window
    - first_at / last_at: ISO timestamps of earliest and latest snapshot
    - tier_distribution: per-tier member counts
    - total_members: sum of all tier counts
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Verify perspective exists and get its name
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    perspective_name = perspective.name

    # Fetch all snapshots in the window for this perspective
    snapshots = (
        session.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.perspective_id == perspective_id,
            PerspectiveSnapshot.taken_at >= cutoff,
        )
        .order_by(PerspectiveSnapshot.taken_at)
        .all()
    )

    snapshot_count = len(snapshots)

    first_at: Optional[str] = None
    last_at: Optional[str] = None
    tier_counts: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
    total_members = 0

    if snapshots:
        first_at = snapshots[0].taken_at.isoformat() if snapshots[0].taken_at else None
        last_at = snapshots[-1].taken_at.isoformat() if snapshots[-1].taken_at else None

        for snap in snapshots:
            members = _parse_membership(snap.membership)
            for member in members:
                tier = _tier_from_member(member)
                if tier not in tier_counts:
                    tier_counts[tier] = 0
                tier_counts[tier] += 1
                total_members += 1

    return RollupResponse(
        perspective_id=perspective_id,
        perspective_name=perspective_name,
        window_days=days,
        snapshot_count=snapshot_count,
        first_at=first_at,
        last_at=last_at,
        tier_distribution=TierDistribution(**tier_counts),
        total_members=total_members,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory SQLite with StaticPool (same thread, no connection limit)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create all tables from SQLAlchemy models
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build a minimal FastAPI app and mount the router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as db:
        p1 = Perspective(
            id=1,
            org_id=100,
            name="Production Servers",
            description="All prod MCP servers",
            facet_filters={},
            created_by=1,
            created_at=now,
            updated_at=now,
        )
        p2 = Perspective(
            id=2,
            org_id=100,
            name="Dev Servers",
            description="Dev/test MCP servers",
            facet_filters={},
            created_by=1,
            created_at=now,
            updated_at=now,
        )
        db.add_all([p1, p2])

        # Perspective 1: 3 snapshots spread over the last 25 days
        # Expected rollup (30-day window): high=3, medium=3, low=2, critical=1
        snap_data_p1 = [
            # day 1 ago  – 1 high, 1 medium, 1 low
            [
                {"server_id": "srv-001", "risk_tier": "high"},
                {"server_id": "srv-002", "risk_tier": "medium"},
                {"server_id": "srv-003", "risk_tier": "low"},
            ],
            # day 10 ago – 1 high, 2 medium, 1 critical
            [
                {"server_id": "srv-004", "risk_tier": "high"},
                {"server_id": "srv-005", "risk_tier": "medium"},
                {"server_id": "srv-006", "risk_tier": "medium"},
                {"server_id": "srv-006b", "risk_tier": "critical"},
            ],
            # day 25 ago – 1 low
            [
                {"server_id": "srv-007", "risk_tier": "low"},
            ],
        ]

        # Perspective 2: 2 snapshots
        snap_data_p2 = [
            # day 5 ago – 1 high, 1 none (no tier)
            [
                {"server_id": "dev-001", "risk_tier": "high"},
                {"server_id": "dev-002"},  # no risk_tier key
            ],
            # day 20 ago – 1 low
            [
                {"server_id": "dev-003", "risk_tier": "low"},
            ],
        ]

        for i, members in enumerate(snap_data_p1):
            snap = PerspectiveSnapshot(
                perspective_id=1,
                taken_at=now - timedelta(days=[1, 10, 25][i]),
                membership=json.dumps(members),
            )
            db.add(snap)

        for i, members in enumerate(snap_data_p2):
            snap = PerspectiveSnapshot(
                perspective_id=2,
                taken_at=now - timedelta(days=[5, 20][i]),
                membership=json.dumps(members),
            )
            db.add(snap)

        db.commit()

    client = TestClient(app)

    # ---- Happy path: perspective 1, 30-day window ----
    r = client.get("/api/perspectives/1/snapshots/rollup?days=30")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert data["perspective_id"] == 1
    assert data["perspective_name"] == "Production Servers"
    assert data["window_days"] == 30
    assert data["snapshot_count"] == 3, f"Expected 3 snapshots, got {data['snapshot_count']}"
    assert data["total_members"] == 8, f"Expected 8 total members, got {data['total_members']}"

    td = data["tier_distribution"]
    assert td["high"] == 3, f"Expected high=3, got {td['high']}"
    assert td["medium"] == 2, f"Expected medium=2, got {td['medium']}"
    assert td["low"] == 2, f"Expected low=2, got {td['low']}"
    assert td["critical"] == 1, f"Expected critical=1, got {td['critical']}"
    print("  - GET /api/perspectives/1/snapshots/rollup?days=30: PASS")

    # ---- Same perspective, 15-day window (excludes the day-25 snapshot) ----
    r = client.get("/api/perspectives/1/snapshots/rollup?days=15")
    assert r.status_code == 200
    data = r.json()
    assert data["snapshot_count"] == 2, f"Expected 2 snapshots in 15-day window, got {data['snapshot_count']}"
    assert data["total_members"] == 6, f"Expected 6 members in 15-day window, got {data['total_members']}"
    print("  - GET /api/perspectives/1/snapshots/rollup?days=15: PASS")

    # ---- Perspective 2 ----
    r = client.get("/api/perspectives/2/snapshots/rollup?days=30")
    assert r.status_code == 200
    data = r.json()
    assert data["perspective_id"] == 2
    assert data["perspective_name"] == "Dev Servers"
    assert data["snapshot_count"] == 2
    assert data["tier_distribution"]["high"] == 1
    assert data["tier_distribution"]["low"] == 1
    assert data["tier_distribution"]["none"] == 1, "Server with no risk_tier key should fall through to 'none'"
    print("  - GET /api/perspectives/2/snapshots/rollup?days=30: PASS")

    # ---- 404 for unknown perspective ----
    r = client.get("/api/perspectives/999/snapshots/rollup")
    assert r.status_code == 404, f"Expected 404 for unknown perspective, got {r.status_code}"
    print("  - GET /api/perspectives/999/snapshots/rollup -> 404: PASS")

    # ---- Edge: perspective with no snapshots ----
    with TestingSessionLocal() as db:
        p_empty = Perspective(
            id=3,
            org_id=100,
            name="Empty Perspective",
            created_by=1,
            created_at=now,
            updated_at=now,
        )
        db.add(p_empty)
        db.commit()

    r = client.get("/api/perspectives/3/snapshots/rollup")
    assert r.status_code == 200
    data = r.json()
    assert data["snapshot_count"] == 0
    assert data["total_members"] == 0
    assert data["tier_distribution"]["high"] == 0
    print("  - GET /api/perspectives/3/snapshots/rollup (empty): PASS")

    print("\nPASS")
