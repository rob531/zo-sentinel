# services/staged/risk_watchlist/logic.py

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select, asc
from sqlalchemy.orm import Session, aliased
from typing import List, Literal, Optional, Dict, Any

from app.db import get_session, Base
from app.models import McpServerRegistry, McpLlmAxisScore

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

RiskTier = Literal[
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
]

def get_watchlist(
    tier: Optional[RiskTier] = Query(
        None,
        description="Filter by risk tier",
        regex="^(TRUSTED_GENERAL|TRUSTED_RESEARCH|ENTERPRISE_CONTROLLED|CAUTION_LIMITED|HIGH_RISK_ISOLATED|KNOWN_THREAT)$",
    ),
    session: Session = Depends(get_session),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Retrieve servers from the registry, optionally filtered by risk tier,
    ordered by ``trust_score`` ascending and enriched with the most recent
    ``scored_at`` timestamp from ``mcp_llm_axis_scores``.
    """
    # ------------------------------------------------------------------- #
    # Sub‑query: latest scored_at per server
    # ------------------------------------------------------------------- #
    latest_score_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )
    # Alias for clarity
    latest_score = aliased(latest_score_subq)

    # ------------------------------------------------------------------- #
    # Base query on the server registry
    # ------------------------------------------------------------------- #
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            latest_score.c.last_scored,
        )
        .outerjoin(latest_score, McpServerRegistry.server_id == latest_score.c.server_id)
        .order_by(asc(McpServerRegistry.trust_score))
    )

    if tier is not None:
        stmt = stmt.where(McpServerRegistry.risk_tier == tier)

    rows = session.execute(stmt).all()

    servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "trust_score": row.trust_score,
            "last_scored": row.last_scored,
            "verdict": row.verdict,
        }
        for row in rows
    ]

    return {"servers": servers}


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # NOTE: The self‑test builds an in‑memory SQLite DB that mimics the real
    # schema.  It overrides the ``get_session`` dependency with a session bound
    # to this temporary engine.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create temporary engine & session
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    # Create tables according to the real models
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed data (4 servers across 3 tiers)
    # ------------------------------------------------------------------- #
    seed_servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="Alpha",
            risk_tier="TRUSTED_GENERAL",
            trust_score=10.0,
            verdict="ALLOW",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="Beta",
            risk_tier="TRUSTED_RESEARCH",
            trust_score=30.0,
            verdict="ALLOW",
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="Gamma",
            risk_tier="ENTERPRISE_CONTROLLED",
            trust_score=20.0,
            verdict="ALLOW",
        ),
        McpServerRegistry(
            server_id="srv-4",
            name="Delta",
            risk_tier="TRUSTED_GENERAL",
            trust_score=5.0,
            verdict="ALLOW",
        ),
    ]

    # Insert seed data
    with SessionLocal() as sess:
        sess.add_all(seed_servers)
        sess.commit()

        # ------------------------------------------------------------------- #
        # Invoke the logic under test
        # ------------------------------------------------------------------- #
        result = get_watchlist(tier="TRUSTED_GENERAL", session=sess)

        # Expected: two servers (srv-4, srv-1) ordered by trust_score ASC
        expected_ids = ["srv-4", "srv-1"]
        returned_ids = [s["server_id"] for s in result["servers"]]

        if returned_ids == expected_ids:
            print("PASS")
        else:
            raise AssertionError(
                f"Watchlist returned unexpected order/contents: {returned_ids}"
            )