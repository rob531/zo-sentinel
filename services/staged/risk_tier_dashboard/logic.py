"""Risk Tier Dashboard logic.

Provides an endpoint that returns the distribution of MCP servers across
different risk tiers.
"""

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class RiskTierDistribution(BaseModel):
    """Pydantic model for a single risk‑tier bucket."""
    risk_tier: str
    count: int


def get_risk_tier_distribution(session: Session) -> List[dict]:
    """Aggregate MCP servers by their risk tier.

    Args:
        session: SQLAlchemy session bound to the application database.

    Returns:
        A list of dictionaries ``{'risk_tier': <str>, 'count': <int>}`` describing
        how many servers belong to each tier.
    """
    rows = (
        session.query(McpServerRegistry.risk_tier, func.count().label("cnt"))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    return [{"risk_tier": r[0], "count": r[1]} for r in rows]


@router.get(
    "/dashboard/risk-tier",
    response_model=List[RiskTierDistribution],
    summary="Risk‑tier distribution dashboard",
)
def risk_tier_dashboard_endpoint(session: Session = Depends(get_session)):
    """FastAPI endpoint returning the risk‑tier distribution."""
    return get_risk_tier_distribution(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test creates an in‑memory SQLite database, populates it with a
    # few sample rows and verifies that the aggregation logic works as expected.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the declarative base to create tables in the temporary engine.
    from app.models import Base, McpServerRegistry

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db: Session = SessionLocal()

    # Insert sample data – column names are taken from the real model.
    sample_data = [
        McpServerRegistry(
            server_id="srv-1",
            risk_tier="high",
            confidence=0.95,
            description="sample server 1",
        ),
        McpServerRegistry(
            server_id="srv-2",
            risk_tier="low",
            confidence=0.80,
            description="sample server 2",
        ),
        McpServerRegistry(
            server_id="srv-3",
            risk_tier="high",
            confidence=0.60,
            description="sample server 3",
        ),
    ]
    db.add_all(sample_data)
    db.commit()

    # Run the aggregation.
    result = get_risk_tier_distribution(db)

    # Expected outcome.
    expected = [
        {"risk_tier": "high", "count": 2},
        {"risk_tier": "low", "count": 1},
    ]

    # Simple assertion – order does not matter.
    assert sorted(result, key=lambda x: x["risk_tier"]) == sorted(
        expected, key=lambda x: x["risk_tier"]
    )
    print("PASS")