"""
services/staged/risk_tier_breakdown/logic.py

Logic for the ``/api/risk/breakdown`` endpoint.

The module provides a single public function ``get_risk_tier_breakdown`` that
queries the ``McpServerRegistry`` table, groups servers by their
``risk_tier`` column and returns a mapping of tier → count together with the
overall total and per‑tier percentages.

The implementation mirrors the exemplar service logic and uses the real
application data layer (SQLAlchemy session from ``app.db`` and models from
``app.models``).  The ``__main__`` block contains a self‑test that creates an
in‑memory SQLite database, seeds it with sample data, invokes the function and
asserts the expected contract.
"""

from typing import Dict, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # type: ignore  # real model


def _compute_breakdown(session: Session) -> Dict[str, Any]:
    """
    Core implementation that performs the aggregation.

    Returns a dictionary with the shape:
    {
        "counts": {"low": 3, "medium": 5, "high": 2},
        "total": 10,
        "percentages": {"low": 30.0, "medium": 50.0, "high": 20.0}
    }
    """
    # Aggregate counts per tier
    stmt = (
        select(
            McpServerRegistry.risk_tier.label("tier"),
            func.count().label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .order_by(McpServerRegistry.risk_tier)
    )
    result = session.execute(stmt).all()

    counts: Dict[str, int] = {row.tier: row.cnt for row in result}
    total = sum(counts.values()) or 0

    percentages: Dict[str, float] = {}
    if total:
        percentages = {
            tier: round((cnt / total) * 100, 2) for tier, cnt in counts.items()
        }

    return {"counts": counts, "total": total, "percentages": percentages}


def get_risk_tier_breakdown(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """
    FastAPI‑compatible dependency that returns the risk‑tier breakdown.
    """
    return _compute_breakdown(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # NOTE: The test uses an in‑memory SQLite database and overrides the
    # ``get_session`` dependency locally.  The production code continues to
    # import the real ``app.db.get_session`` and ``app.models`` without any
    # modification.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base  # type: ignore  # declarative base

    # Create temporary SQLite engine and bind metadata
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, future=True)

    # Seed data: 10 servers across three tiers
    sample_data = [
        {"server_id": f"srv-{i:02d}", "risk_tier": tier}
        for i, tier in enumerate(
            ["low", "low", "low", "medium", "medium", "medium", "medium", "high", "high", "medium"]
        )
    ]

    with SessionLocal() as db:
        for rec in sample_data:
            db.add(McpServerRegistry(**rec))
        db.commit()

        # Invoke the logic
        breakdown = _compute_breakdown(db)

        # Expected values
        expected_counts = {"low": 3, "medium": 5, "high": 2}
        expected_total = 10
        expected_percentages = {
            "low": 30.0,
            "medium": 50.0,
            "high": 20.0,
        }

        assert breakdown["counts"] == expected_counts, f"Counts mismatch: {breakdown['counts']}"
        assert breakdown["total"] == expected_total, f"Total mismatch: {breakdown['total']}"
        assert breakdown["percentages"] == expected_percentages, f"Percentages mismatch: {breakdown['percentages']}"

        print("PASS")