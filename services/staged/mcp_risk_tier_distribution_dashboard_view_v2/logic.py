"""
Logic for the MCP Risk Tier Distribution Dashboard View (v2).

Provides a single callable that aggregates the count of servers per risk tier
using the authoritative application database tables.
"""

from typing import Dict

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

# Real application data layer imports – must remain unchanged for production.
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401 (imported for side‑effects / future use)


def _detect_tier_column() -> str:
    """
    Detect the column on ``McpServerRegistry`` that stores the risk tier.
    The column name is expected to contain the word ``tier`` (case‑insensitive).
    """
    for col in McpServerRegistry.__table__.c:
        if "tier" in col.name.lower():
            return col.name
    raise RuntimeError("Risk tier column not found in McpServerRegistry model.")


def get_risk_tier_distribution(session: Session = Depends(get_session)) -> Dict[str, Dict[str, int]]:
    """
    Aggregate the number of servers for each risk tier.

    Returns
    -------
    dict
        Structure compatible with the router response model:
        ``{ "tiers": { "<tier>": <count>, ... } }``.
    """
    tier_col_name = _detect_tier_column()
    tier_col = getattr(McpServerRegistry, tier_col_name)

    stmt = (
        session.query(tier_col, func.count().label("cnt"))
        .group_by(tier_col)
        .order_by(tier_col)
    )
    result = {row[0]: row[1] for row in stmt.all()}

    return {"tiers": result}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite engine and bind a sessionmaker to it.
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    # Create all tables defined in the metadata of the imported models.
    # This respects the real model definitions (no stubs).
    McpServerRegistry.metadata.create_all(engine)  # type: ignore[attr-defined]

    # Detect the tier column and primary key column for dynamic seeding.
    tier_col_name = _detect_tier_column()
    pk_col = list(McpServerRegistry.__table__.primary_key)[0].name

    # Seed five servers with distinct risk tiers.
    seed_data = [
        {"server_id": "srv-1", "tier": "low"},
        {"server_id": "srv-2", "tier": "medium"},
        {"server_id": "srv-3", "tier": "high"},
        {"server_id": "srv-4", "tier": "critical"},
        {"server_id": "srv-5", "tier": "low"},
    ]

    with SessionLocal() as sess:
        for entry in seed_data:
            model_kwargs = {
                pk_col: entry["server_id"],
                tier_col_name: entry["tier"],
            }
            sess.add(McpServerRegistry(**model_kwargs))
        sess.commit()

        # Invoke the logic under test.
        distribution = get_risk_tier_distribution(sess)

    # Expected counts.
    expected = {
        "low": 2,
        "medium": 1,
        "high": 1,
        "critical": 1,
    }

    assert distribution["tiers"] == expected, f"Unexpected distribution: {distribution}"
    print("PASS")