"""logic.py -- data/computation for the registry_verdict_cohort_compare_api service.

Provides a pure function that aggregates McpServerRegistry rows into per‑source
cohorts, counting verdicts, risk tiers and averaging trust scores over a recent
window (default 30 days).  The router layer supplies the FastAPI session
dependency; this module contains no FastAPI code.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def cohort_compare(db: Session, days: int = 30) -> Dict[str, object]:
    """Return verdict‑ and tier‑breakdown per registry_source.

    Rows are filtered to those whose ``last_assessed`` is within the last *days*
    days (relative to UTC now).  The result shape matches the contract:

    {
        "days": int,
        "cohorts": [
            {
                "source": str,
                "total": int,
                "verdict_counts": {verdict: int, ...},
                "tier_counts": {risk_tier: int, ...},
                "mean_trust_score": float,
            },
            ...
        ],
    }
    """
    cutoff: datetime = datetime.utcnow() - timedelta(days=days)

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        ).where(McpServerRegistry.last_assessed >= cutoff)
    ).all()

    # aggregate in‑memory
    agg: Dict[str, dict] = {}
    for source, verdict, tier, trust in rows:
        src_key = source or "unknown"
        if src_key not in agg:
            agg[src_key] = {
                "total": 0,
                "verdict_counts": {},
                "tier_counts": {},
                "trust_sum": 0.0,
                "trust_n": 0,
            }

        cohort = agg[src_key]
        cohort["total"] += 1

        v_key = verdict or "unknown"
        cohort["verdict_counts"][v_key] = cohort["verdict_counts"].get(v_key, 0) + 1

        t_key = tier or "unknown"
        cohort["tier_counts"][t_key] = cohort["tier_counts"].get(t_key, 0) + 1

        if trust is not None:
            cohort["trust_sum"] += float(trust)
            cohort["trust_n"] += 1

    # build final structure
    cohorts: List[dict] = []
    for source, info in agg.items():
        mean_trust = (
            info["trust_sum"] / info["trust_n"]
            if info["trust_n"] > 0
            else 0.0
        )
        cohorts.append(
            {
                "source": source,
                "total": info["total"],
                "verdict_counts": info["verdict_counts"],
                "tier_counts": info["tier_counts"],
                "mean_trust_score": mean_trust,
            }
        )

    return {"days": days, "cohorts": cohorts}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    # in‑memory SQLite for isolated testing
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    db = SessionLocal()

    now = datetime.utcnow()
    sample_rows = [
        # source A – two verdicts, two tiers
        McpServerRegistry(
            server_id="a1",
            registry_source="srcA",
            verdict="good",
            risk_tier="high",
            trust_score=0.80,
            last_assessed=now - timedelta(days=5),
        ),
        McpServerRegistry(
            server_id="a2",
            registry_source="srcA",
            verdict="bad",
            risk_tier="low",
            trust_score=0.40,
            last_assessed=now - timedelta(days=5),
        ),
        # source B – single verdict, single tier
        McpServerRegistry(
            server_id="b1",
            registry_source="srcB",
            verdict="good",
            risk_tier="high",
            trust_score=0.90,
            last_assessed=now - timedelta(days=10),
        ),
        # source C – within window, distinct tier
        McpServerRegistry(
            server_id="c1",
            registry_source="srcC",
            verdict="bad",
            risk_tier="low",
            trust_score=0.55,
            last_assessed=now - timedelta(days=15),
        ),
    ]

    db.add_all(sample_rows)
    db.commit()

    result = cohort_compare(db, days=30)

    # basic contract assertions
    assert result["days"] == 30
    assert len(result["cohorts"]) == 3

    srcA = next(c for c in result["cohorts"] if c["source"] == "srcA")
    assert srcA["total"] == 2
    assert srcA["verdict_counts"]["good"] == 1
    assert srcA["verdict_counts"]["bad"] == 1
    expected_mean_a = (0.80 + 0.40) / 2
    assert abs(srcA["mean_trust_score"] - expected_mean_a) < 0.01

    srcB = next(c for c in result["cohorts"] if c["source"] == "srcB")
    assert srcB["total"] == 1
    assert srcB["verdict_counts"]["good"] == 1

    srcC = next(c for c in result["cohorts"] if c["source"] == "srcC")
    assert srcC["total"] == 1
    assert srcC["verdict_counts"]["bad"] == 1

    print("PASS")