"""Logic for the server risk tier distribution dashboard."""

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from pydantic import BaseModel
from typing import List

# Real application data layer imports
from app.db import get_session, Base
from app.models import *  # noqa: F403,F401  (import all model classes)


def _model_by_tablename(tablename: str):
    """Return the declarative model class matching the given table name."""
    for cls in Base._decl_class_registry.values():
        if hasattr(cls, "__tablename__") and cls.__tablename__ == tablename:
            return cls
    raise LookupError(f"Model for table '{tablename}' not found.")


# Resolve the concrete models from the real app schema
ServerModel = _model_by_tablename("McpServerRegistry")


class TierItem(BaseModel):
    tier: str
    count: int
    percentage: float


class TierDistribution(BaseModel):
    tiers: List[TierItem]


def get_risk_tier_distribution(
    session: Session = Depends(get_session),
) -> TierDistribution:
    """
    Compute the distribution of servers across risk tiers.

    Returns a `TierDistribution` containing a list of tiers with their
    respective counts and percentages of the total server population.
    """
    # Aggregate counts per tier
    rows = (
        session.query(ServerModel.risk_tier, func.count(ServerModel.id))
        .group_by(ServerModel.risk_tier)
        .all()
    )

    total = sum(count for _, count in rows) or 1  # avoid division by zero

    tier_items = [
        TierItem(
            tier=tier,
            count=count,
            percentage=round((count / total) * 100, 2),
        )
        for tier, count in rows
    ]

    return TierDistribution(tiers=tier_items)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for isolated testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data: three servers, each with a distinct risk tier
    with SessionLocal() as test_session:
        server_a = ServerModel(risk_tier="low")
        server_b = ServerModel(risk_tier="medium")
        server_c = ServerModel(risk_tier="high")
        test_session.add_all([server_a, server_b, server_c])
        test_session.commit()

        # Invoke the core logic
        result = get_risk_tier_distribution(session=test_session)

        # Basic assertions per the acceptance criteria
        assert isinstance(result, TierDistribution)
        assert len(result.tiers) == 3

        # Find the entry for the known tier "low"
        low_entry = next((t for t in result.tiers if t.tier == "low"), None)
        assert low_entry is not None
        assert low_entry.count == 1

        print("PASS")