"""services/staged/risk_tier_source_matrix_api/logic.py

Logic for the risk tier source matrix endpoint.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean
from typing import Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel

from app.db import get_session, Base
from app.models import McpServerRegistry
from sqlalchemy.orm import Session


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class SourceInfo(BaseModel):
    registry_source: str
    total: int
    unscored: int
    mean_trust_score: Optional[float] = None
    tiers: Dict[str, int]


class TierMatrixResponse(BaseModel):
    total_servers: int
    sources: List[SourceInfo]


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def compute_risk_tier_source_matrix(session: Session) -> TierMatrixResponse:
    """Compute the risk‑tier‑by‑source matrix.

    Returns a ``TierMatrixResponse`` instance.
    """
    rows = session.query(McpServerRegistry).all()

    total_servers = len(rows)

    # aggregation structures
    source_agg: Dict[
        str,
        dict,
    ] = defaultdict(
        lambda: {
            "total": 0,
            "unscored": 0,
            "trust_scores": [],
            "tiers": defaultdict(int),
        }
    )

    for r in rows:
        src = r.registry_source or ""
        agg = source_agg[src]
        agg["total"] += 1

        # risk tier handling
        if r.risk_tier is None:
            agg["unscored"] += 1
        else:
            agg["tiers"][r.risk_tier] += 1

        # trust score handling
        if r.trust_score is not None:
            agg["trust_scores"].append(float(r.trust_score))

    # build ordered list of SourceInfo
    sources: List[SourceInfo] = []
    for src in sorted(source_agg.keys()):
        agg = source_agg[src]
        mean_ts: Optional[float] = None
        if agg["trust_scores"]:
            mean_ts = fmean(agg["trust_scores"])
        sources.append(
            SourceInfo(
                registry_source=src,
                total=agg["total"],
                unscored=agg["unscored"],
                mean_trust_score=mean_ts,
                tiers=dict(agg["tiers"]),
            )
        )

    return TierMatrixResponse(total_servers=total_servers, sources=sources)


# Dependency‑injected wrapper used by the router
def get_risk_tier_source_matrix(
    session: Session = Depends(get_session),
) -> TierMatrixResponse:
    """FastAPI dependency that returns the matrix."""
    return compute_risk_tier_source_matrix(session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Self‑test builds an in‑memory SQLite DB, populates it, runs the logic,
    # and validates the contract described in the task.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # create in‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # create tables
    Base.metadata.create_all(engine)

    # seed data
    test_rows = [
        {
            "server_id": "srv-1",
            "name": "server‑1",
            "registry_source": "source‑A",
            "risk_tier": "TRUSTED_GENERAL",
            "verdict": "ALLOW",
            "trust_score": 80.0,
            "last_assessed": None,
            "last_seen": None,
        },
        {
            "server_id": "srv-2",
            "name": "server‑2",
            "registry_source": "source‑A",
            "risk_tier": "HIGH_RISK_ISOLATED",
            "verdict": "BLOCK",
            "trust_score": 20.0,
            "last_assessed": None,
            "last_seen": None,
        },
        {
            "server_id": "srv-3",
            "name": "server‑3",
            "registry_source": "source‑B",
            "risk_tier": "TRUSTED_RESEARCH",
            "verdict": "ALLOW",
            "trust_score": 70.0,
            "last_assessed": None,
            "last_seen": None,
        },
        {
            "server_id": "srv-4",
            "name": "server‑4",
            "registry_source": "source‑B",
            "risk_tier": None,
            "verdict": "UNKNOWN",
            "trust_score": None,
            "last_assessed": None,
            "last_seen": None,
        },
    ]

    with SessionLocal() as sess:
        for row in test_rows:
            sess.add(McpServerRegistry(**row))
        sess.commit()

        result = compute_risk_tier_source_matrix(sess)

    # ------------------------------------------------------------------- #
    # Assertions
    # ------------------------------------------------------------------- #
    assert result.total_servers == 4, "total_servers should be 4"

    # source‑A checks
    src_a = next(s for s in result.sources if s.registry_source == "source‑A")
    assert src_a.total == 2, "source‑A total should be 2"
    assert src_a.unscored == 0, "source‑A unscored should be 0"
    assert round(src_a.mean_trust_score or 0, 5) == 50.0, "source‑A mean trust score mismatch"
    assert src_a.tiers == {
        "TRUSTED_GENERAL": 1,
        "HIGH_RISK_ISOLATED": 1,
    }, "source‑A tier counts mismatch"

    # source‑B checks
    src_b = next(s for s in result.sources if s.registry_source == "source‑B")
    assert src_b.total == 2, "source‑B total should be 2"
    assert src_b.unscored == 1, "source‑B unscored should be 1"
    assert round(src_b.mean_trust_score or 0, 5) == 70.0, "source‑B mean trust score mismatch"
    assert src_b.tiers == {"TRUSTED_RESEARCH": 1}, "source‑B tier counts mismatch"

    print("PASS")