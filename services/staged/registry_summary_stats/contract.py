# services/staged/registry_summary_stats/contract.py
from __future__ import annotations

import datetime
from datetime import timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class RecentServer(BaseModel):
    server_id: str = Field(..., alias="server_id")
    name: str
    risk_tier: str
    last_scanned: datetime.datetime


class SummaryResponse(BaseModel):
    total: int
    tiers: Dict[str, int]
    freshness: Dict[str, int]
    recent: List[RecentServer]


@router.get("/registry/summary", response_model=SummaryResponse)
def get_registry_summary(session: Session = Depends(get_session)) -> SummaryResponse:
    """Compute registry summary statistics."""
    now = datetime.datetime.utcnow()

    # total servers
    total = session.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()

    # tier breakdown
    tier_rows = session.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()
    tiers = {tier: count for tier, count in tier_rows}

    # freshness windows based on last_assessed
    def count_within(delta: timedelta) -> int:
        cutoff = now - delta
        return session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_assessed >= cutoff)
        ).scalar_one()

    freshness = {
        "window_24h": count_within(timedelta(hours=24)),
        "window_7d": count_within(timedelta(days=7)),
        "window_30d": count_within(timedelta(days=30)),
    }

    # most recent scans
    recent_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
        )
        .order_by(McpServerRegistry.last_scanned.desc())
        .limit(5)
    ).all()
    recent = [
        RecentServer(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            last_scanned=row.last_scanned,
        )
        for row in recent_rows
    ]

    return SummaryResponse(
        total=total,
        tiers=tiers,
        freshness=freshness,
        recent=recent,
    )


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.registry_summary_stats.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine for the test
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Override the dependency to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Seed data: 5 servers across 3 tiers with varying last_scanned / last_assessed
    now = datetime.datetime.utcnow()
    seed_servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="Alpha",
            risk_tier="high",
            last_scanned=now - timedelta(hours=1),
            last_assessed=now - timedelta(hours=2),
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="Beta",
            risk_tier="medium",
            last_scanned=now - timedelta(days=1, hours=3),
            last_assessed=now - timedelta(days=1),
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="Gamma",
            risk_tier="low",
            last_scanned=now - timedelta(days=5),
            last_assessed=now - timedelta(days=6),
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="Delta",
            risk_tier="high",
            last_scanned=now - timedelta(days=10),
            last_assessed=now - timedelta(days=15),
        ),
        McpServerRegistry(
            server_id="srv-5",
            name="Epsilon",
            risk_tier="medium",
            last_scanned=now - timedelta(days=20),
            last_assessed=now - timedelta(days=25),
        ),
    ]

    with TestSessionLocal() as sess:
        sess.add_all(seed_servers)
        sess.commit()

    client = TestClient(app)

    resp = client.get("/api/registry/summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic assertions
    assert data["total"] == 5, "Total count mismatch"
    expected_tiers = {"high": 2, "medium": 2, "low": 1}
    assert data["tiers"] == expected_tiers, f"Tiers mismatch: {data['tiers']}"
    for key in ("window_24h", "window_7d", "window_30d"):
        assert isinstance(data["freshness"][key], int) and data["freshness"][key] >= 0

    # Recent list should be ordered by last_scanned descending
    recent_ids = [item["server_id"] for item in data["recent"]]
    assert recent_ids == ["srv-1", "srv-2", "srv-3", "srv-4", "srv-5"][: len(recent_ids)]

    print("PASS")