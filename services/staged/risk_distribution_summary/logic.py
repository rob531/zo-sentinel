# services/staged/risk_distribution_summary/logic.py

from collections import Counter
from typing import Dict

from fastapi import Depends

from app.db import get_session
from app.models import McpServerRegistry
from pydantic import BaseModel


class DistributionResponse(BaseModel):
    distribution: Dict[str, int]
    total_servers: int


def get_risk_distribution(
    session=Depends(get_session),
) -> DistributionResponse:
    """
    Compute the distribution of `risk_tier` values across all servers.

    Returns:
        DistributionResponse: Mapping of tier -> count and total server count.
    """
    tiers = [
        row[0]
        for row in session.query(McpServerRegistry.risk_tier).all()
        if row[0] is not None
    ]
    counter = Counter(tiers)
    distribution = {str(k): v for k, v in counter.items()}
    total = sum(counter.values())
    return DistributionResponse(distribution=distribution, total_servers=total)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # NOTE: The test uses an in‑memory SQLite database and overrides the
    #       application session dependency. This does **not** affect the
    #       production implementation which always uses the real Postgres DB.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the declarative base to create tables in the temporary DB.
    from app.db import Base

    # Create temporary SQLite engine and session factory.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data with a known tier distribution.
    test_data = [
        {"server_id": 1, "risk_tier": "low"},
        {"server_id": 2, "risk_tier": "low"},
        {"server_id": 3, "risk_tier": "medium"},
        {"server_id": 4, "risk_tier": "high"},
        {"server_id": 5, "risk_tier": "high"},
        {"server_id": 6, "risk_tier": "high"},
    ]

    with SessionLocal() as session:
        for row in test_data:
            session.add(McpServerRegistry(**row))
        session.commit()

        # Invoke the logic under test.
        result = get_risk_distribution(session)

        # Expected distribution.
        expected_distribution = {"low": 2, "medium": 1, "high": 3}
        expected_total = 6

        assert result.distribution == expected_distribution, (
            f"Distribution mismatch: {result.distribution} != {expected_distribution}"
        )
        assert result.total_servers == expected_total, (
            f"Total servers mismatch: {result.total_servers} != {expected_total}"
        )

    print("PASS")