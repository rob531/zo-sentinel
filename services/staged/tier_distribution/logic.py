# services/staged/tier_distribution/logic.py
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierStat(BaseModel):
    tier: str
    count: int
    pct: float


class TierDistributionResponse(BaseModel):
    tiers: List[TierStat]
    total: int
    as_of: datetime


@router.get(
    "/api/servers/tier-distribution",
    response_model=TierDistributionResponse,
    tags=["tier_distribution"],
)
def get_tier_distribution(
    registry_source: Optional[str] = Query(None, description="Filter by registry source"),
    days: Optional[int] = Query(
        None, ge=1, description="Only include servers seen in the last N days"
    ),
    db: Session = Depends(get_session),
) -> TierDistributionResponse:
    """
    Return a distribution of servers across risk tiers.
    """
    stmt = select(McpServerRegistry.risk_tier, func.count().label("cnt")).group_by(
        McpServerRegistry.risk_tier
    )

    if registry_source is not None:
        stmt = stmt.where(McpServerRegistry.registry_source == registry_source)

    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = stmt.where(McpServerRegistry.last_seen >= cutoff)

    rows = db.execute(stmt).all()

    tier_counts = {row[0]: row[1] for row in rows}
    total = sum(tier_counts.values())

    tiers = [
        TierStat(
            tier=tier,
            count=count,
            pct=round((count / total) * 100, 2) if total else 0.0,
        )
        for tier, count in tier_counts.items()
    ]

    return TierDistributionResponse(
        tiers=tiers,
        total=total,
        as_of=datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a minimal FastAPI app for testing
    app = FastAPI()
    app.include_router(router)

    # Create an in‑memory SQLite DB that mirrors the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables using the real model metadata
    McpServerRegistry.metadata.create_all(bind=engine)

    # Dependency override to use the test session
    def override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed the test DB with 5 servers across 4 distinct tiers
    now = datetime.utcnow()
    test_servers = [
        McpServerRegistry(
            server_id="s1",
            name="srv-1",
            url="http://s1",
            risk_tier="TRUSTED_GENERAL",
            registry_source="srcA",
            last_seen=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="s2",
            name="srv-2",
            url="http://s2",
            risk_tier="TRUSTED_RESEARCH",
            registry_source="srcA",
            last_seen=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="s3",
            name="srv-3",
            url="http://s3",
            risk_tier="ENTERPRISE_CONTROLLED",
            registry_source="srcB",
            last_seen=now - timedelta(days=3),
        ),
        McpServerRegistry(
            server_id="s4",
            name="srv-4",
            url="http://s4",
            risk_tier="HIGH_RISK_ISOLATED",
            registry_source="srcB",
            last_seen=now - timedelta(days=4),
        ),
        McpServerRegistry(
            server_id="s5",
            name="srv-5",
            url="http://s5",
            risk_tier="HIGH_RISK_ISOLATED",
            registry_source="srcA",
            last_seen=now - timedelta(days=5),
        ),
    ]

    # Insert seed data
    with SessionLocal() as db:
        db.add_all(test_servers)
        db.commit()

    client = TestClient(app)

    # Perform request without filters
    resp = client.get("/api/servers/tier-distribution")
    if resp.status_code != 200:
        print(f"❌ Unexpected status code: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    expected_total = 5
    expected_tiers = {
        "TRUSTED_GENERAL": 1,
        "TRUSTED_RESEARCH": 1,
        "ENTERPRISE_CONTROLLED": 1,
        "HIGH_RISK_ISOLATED": 2,
    }

    if data["total"] != expected_total:
        print(
            f"❌ Total mismatch: expected {expected_total}, got {data['total']}",
            file=sys.stderr,
        )
        sys.exit(1)

    if len(data["tiers"]) != len(expected_tiers):
        print(
            f"❌ Tier count mismatch: expected {len(expected_tiers)} tiers, got {len(data['tiers'])}",
            file=sys.stderr,
        )
        sys.exit(1)

    for tier_entry in data["tiers"]:
        tier = tier_entry["tier"]
        count = tier_entry["count"]
        if tier not in expected_tiers:
            print(f"❌ Unexpected tier returned: {tier}", file=sys.stderr)
            sys.exit(1)
        if count != expected_tiers[tier]:
            print(
                f"❌ Count mismatch for {tier}: expected {expected_tiers[tier]}, got {count}",
                file=sys.stderr,
            )
            sys.exit(1)

    print("PASS")