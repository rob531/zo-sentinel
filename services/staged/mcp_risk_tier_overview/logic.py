# services/staged/mcp_risk_tier_overview/logic.py
from typing import List

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry


class TierInfo(BaseModel):
    tier: str = Field(..., description="Risk tier name")
    count: int = Field(..., description="Number of servers in this tier")
    percentage: float = Field(..., description="Percentage of total servers")


class Overview(BaseModel):
    total: int = Field(..., description="Total number of servers")
    tiers: List[TierInfo] = Field(..., description="Breakdown by risk tier")


class OverviewResponse(BaseModel):
    overview: Overview = Field(..., description="Risk tier overview payload")


def _compute_overview(db: Session) -> OverviewResponse:
    """Core implementation – can be called directly with a DB session."""
    total = db.query(func.count()).select_from(McpServerRegistry).scalar() or 0

    tier_rows = (
        db.query(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers: List[TierInfo] = []
    for tier, cnt in tier_rows:
        percent = (cnt / total * 100.0) if total else 0.0
        tiers.append(TierInfo(tier=tier, count=cnt, percentage=percent))

    return OverviewResponse(overview=Overview(total=total, tiers=tiers))


def get_risk_tier_overview(
    db: Session = Depends(get_session),
) -> OverviewResponse:
    """FastAPI dependency‑injected endpoint implementation."""
    return _compute_overview(db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the existing metadata
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Override the FastAPI dependency to use our temporary session
    def _test_session() -> Session:
        return SessionLocal()

    # Populate test data
    test_db = SessionLocal()
    servers = [
        McpServerRegistry(server_id="srv-1", risk_tier="high"),
        McpServerRegistry(server_id="srv-2", risk_tier="high"),
        McpServerRegistry(server_id="srv-3", risk_tier="low"),
    ]
    test_db.add_all(servers)
    test_db.commit()

    # Run the logic
    result = _compute_overview(test_db)

    # Assertions per acceptance criteria
    assert result.overview.total == 3, "total count mismatch"
    assert len(result.overview.tiers) == 2, "unexpected number of tiers"
    high_tier = next(t for t in result.overview.tiers if t.tier == "high")
    assert high_tier.count == 2, "high tier count mismatch"
    # Simple sanity check for percentages
    low_tier = next(t for t in result.overview.tiers if t.tier == "low")
    assert abs(low_tier.percentage - (1 / 3 * 100)) < 0.01, "percentage calculation error"

    print("PASS")