"""logic.py -- data/computation for the registry_verdict_tier_matrix service.

Provides a single pure function that builds a cross‑tabulation of
verdict × risk_tier from the real McpServerRegistry table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_session
from app.models import McpServerRegistry


def get_registry_verdict_tier_matrix(db: Session) -> Dict[str, Any]:
    """Return a matrix of verdict vs risk_tier with counts and mean trust_score.

    The result shape matches the contract expected by the router:

    {
        "generated_from": "mcp_server_registry",
        "total_servers": int,
        "cells": [
            {
                "verdict": str,
                "risk_tier": str,
                "count": int,
                "mean_trust_score": float | None,
            },
            ...
        ],
        "by_source": [
            {"registry_source": str, "count": int},
            ...
        ],
        "unscored": int,
    }
    """
    # total servers
    total_servers = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()

    # cells (verdict × risk_tier) – exclude rows where risk_tier is NULL/empty
    cell_rows = db.execute(
        select(
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            func.count(),
            func.avg(McpServerRegistry.trust_score),
        )
        .where(McpServerRegistry.risk_tier.is_not(None))
        .where(McpServerRegistry.risk_tier != "")
        .group_by(McpServerRegistry.verdict, McpServerRegistry.risk_tier)
    ).all()

    cells: List[Dict[str, Any]] = []
    for verdict, risk_tier, cnt, avg_score in cell_rows:
        cells.append(
            {
                "verdict": verdict,
                "risk_tier": risk_tier,
                "count": int(cnt),
                "mean_trust_score": float(avg_score) if avg_score is not None else None,
            }
        )

    # by_source totals
    source_rows = db.execute(
        select(McpServerRegistry.registry_source, func.count())
        .group_by(McpServerRegistry.registry_source)
    ).all()
    by_source = [{"registry_source": src, "count": int(cnt)} for src, cnt in source_rows]

    # unscored rows (risk_tier NULL or empty)
    unscored = db.execute(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(
            (McpServerRegistry.risk_tier.is_(None)) | (McpServerRegistry.risk_tier == "")
        )
    ).scalar_one()

    return {
        "generated_from": "mcp_server_registry",
        "total_servers": int(total_servers),
        "cells": cells,
        "by_source": by_source,
        "unscored": int(unscored),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite DB that mirrors the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # FastAPI app with overridden dependency for the test session
    app = FastAPI()

    async def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session

    # Seed the test DB
    now = datetime.utcnow()
    seed_data = [
        # verdict "allow", risk_tier "low"
        McpServerRegistry(
            server_id=1,
            name="s1",
            registry_source="srcA",
            verdict="allow",
            risk_tier="low",
            trust_score=0.8,
            confidence=0.9,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
        McpServerRegistry(
            server_id=2,
            name="s2",
            registry_source="srcA",
            verdict="allow",
            risk_tier="low",
            trust_score=0.7,
            confidence=0.8,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
        # verdict "allow", risk_tier "high"
        McpServerRegistry(
            server_id=3,
            name="s3",
            registry_source="srcB",
            verdict="allow",
            risk_tier="high",
            trust_score=0.6,
            confidence=0.7,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
        # verdict "deny", risk_tier "low"
        McpServerRegistry(
            server_id=4,
            name="s4",
            registry_source="srcB",
            verdict="deny",
            risk_tier="low",
            trust_score=0.5,
            confidence=0.6,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
        # verdict "deny", risk_tier "high"
        McpServerRegistry(
            server_id=5,
            name="s5",
            registry_source="srcB",
            verdict="deny",
            risk_tier="high",
            trust_score=0.4,
            confidence=0.5,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
        # unscored row (risk_tier NULL)
        McpServerRegistry(
            server_id=6,
            name="s6",
            registry_source="srcC",
            verdict="allow",
            risk_tier=None,
            trust_score=None,
            confidence=0.5,
            last_assessed=now,
            first_seen=now,
            last_seen=now,
        ),
    ]

    with TestSession() as db:
        db.add_all(seed_data)
        db.commit()

    # Run the computation
    with TestSession() as db:
        result = get_registry_verdict_tier_matrix(db)

    # Assertions per contract
    assert result["total_servers"] == 6, "total_servers mismatch"
    assert result["unscored"] == 1, "unscored count mismatch"

    # Find the cell for verdict='allow' and risk_tier='low'
    target_cell = next(
        (c for c in result["cells"] if c["verdict"] == "allow" and c["risk_tier"] == "low"),
        None,
    )
    assert target_cell is not None, "target cell missing"
    assert target_cell["count"] == 2, "cell count mismatch"

    print("PASS")