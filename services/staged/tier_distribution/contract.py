"""services/staged/tier_distribution/contract.py

FastAPI contract for the tier_distribution service.
Mirrors the pattern used in services/_exemplar/contract.py.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# ----------------------------------------------------------------------
# Real application imports – must not be re‑implemented.
# ----------------------------------------------------------------------
from app.db import Base, get_session  # noqa: F401
from app.models import McpServerRegistry

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class TierCount(BaseModel):
    tier: str = Field(..., description="Risk tier name")
    count: int = Field(..., description="Number of servers in this tier")
    pct: float = Field(..., description="Percentage of total servers")


class TierDistributionResponse(BaseModel):
    tiers: List[TierCount] = Field(..., description="Distribution per tier")
    total: int = Field(..., description="Total number of servers considered")
    as_of: datetime = Field(..., description="Timestamp of the calculation")


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/servers/tier-distribution",
    response_model=TierDistributionResponse,
    summary="Get server tier distribution",
)
def get_tier_distribution(
    registry_source: Optional[str] = Query(
        None, description="Filter by registry source"
    ),
    days: Optional[int] = Query(
        None,
        ge=1,
        description="Only include servers seen in the last N days",
    ),
    session: Session = Depends(get_session),
) -> TierDistributionResponse:
    """Return the distribution of servers across risk tiers."""
    stmt = select(
        McpServerRegistry.risk_tier,
        func.count().label("cnt"),
    ).group_by(McpServerRegistry.risk_tier)

    if registry_source is not None:
        stmt = stmt.where(McpServerRegistry.registry_source == registry_source)

    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = stmt.where(McpServerRegistry.last_seen >= cutoff)

    rows = session.execute(stmt).all()

    tier_counts = [
        TierCount(
            tier=row[0],
            count=row[1],
            pct=0.0,  # placeholder, will be filled after total is known
        )
        for row in rows
    ]

    total = sum(tc.count for tc in tier_counts)
    for tc in tier_counts:
        tc.pct = round((tc.count / total) * 100, 2) if total else 0.0

    return TierDistributionResponse(
        tiers=tier_counts,
        total=total,
        as_of=datetime.utcnow(),
    )


# ----------------------------------------------------------------------
# Self‑test (run with `python -m services.staged.tier_distribution.contract`)
# ----------------------------------------------------------------------
def _create_test_engine() -> Engine:
    """Create an in‑memory SQLite engine suitable for the self‑test."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def _override_get_session(engine: Engine):
    """Dependency override that yields a session bound to the given engine."""
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_test_session() -> Session:
        with TestSessionLocal() as sess:
            yield sess

    return _get_test_session


def _seed_test_data(session: Session) -> None:
    """Insert five servers spanning four distinct risk tiers."""
    now = datetime.utcnow()
    servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="server‑one",
            risk_tier="TRUSTED_GENERAL",
            registry_source="source‑a",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="server‑two",
            risk_tier="TRUSTED_GENERAL",
            registry_source="source‑a",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="server‑three",
            risk_tier="ENTERPRISE_CONTROLLED",
            registry_source="source‑b",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="server‑four",
            risk_tier="HIGH_RISK_ISOLATED",
            registry_source="source‑b",
            last_seen=now,
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="server‑five",
            risk_tier="KNOWN_THREAT",
            registry_source="source‑c",
            last_seen=now,
        ),
    ]
    session.add_all(servers)
    session.commit()


def _run_self_test() -> None:
    """Execute the acceptance self‑test."""
    # Build FastAPI app with the router
    app = FastAPI()
    app.include_router(router)

    # Prepare in‑memory DB and override dependency
    engine = _create_test_engine()
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_session] = _override_get_session(engine)

    # Seed data
    with sessionmaker(bind=engine)() as sess:
        _seed_test_data(sess)

    # Perform request
    client = TestClient(app)
    resp = client.get("/api/servers/tier-distribution")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic shape checks
    assert "tiers" in data and isinstance(data["tiers"], list), "Missing tiers list"
    assert data["total"] == 5, f"Expected total 5, got {data['total']}"
    assert len(data["tiers"]) == 4, f"Expected 4 distinct tiers, got {len(data['tiers'])}"

    # Verify counts per tier
    expected_counts = {
        "TRUSTED_GENERAL": 2,
        "ENTERPRISE_CONTROLLED": 1,
        "HIGH_RISK_ISOLATED": 1,
        "KNOWN_THREAT": 1,
    }
    for tier_info in data["tiers"]:
        tier = tier_info["tier"]
        count = tier_info["count"]
        assert tier in expected_counts, f"Unexpected tier {tier}"
        assert count == expected_counts[tier], f"Tier {tier} count mismatch"

    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    _run_self_test()