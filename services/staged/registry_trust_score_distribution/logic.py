"""logic.py -- data/computation layer for registry_trust_score_distribution service.

Provides a pure function that, given a SQLAlchemy Session, returns a JSON‑serialisable
structure describing the distribution of `trust_score` values in the
`mcp_server_registry` table together with aggregate statistics and a risk‑tier mix.
"""

from __future__ import annotations

from typing import Dict, List, Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import McpServerRegistry
from app.db import Base  # needed for the __main__ self‑test


def trust_score_distribution(db: Session, bucket: int = 10) -> Dict[str, Any]:
    """
    Compute the trust‑score distribution.

    Parameters
    ----------
    db: Session
        Active SQLAlchemy session.
    bucket: int, optional
        Width of each bucket in score points. Clamped to the range [1, 100].
        Default is 10.

    Returns
    -------
    dict
        Structure matching the contract specification:
        {
            "bucket_width": int,
            "total": int,
            "stats": {
                "count": int,
                "scored_count": int,
                "unscored_count": int,
                "min": float | None,
                "max": float | None,
                "mean": float | None,
            },
            "buckets": [
                {"bucket_low": int, "bucket_high": int, "count": int},
                ...
            ],
            "risk_tier_mix": [
                {"risk_tier": str, "count": int},
                ...
            ],
        }
    """
    # Clamp bucket width
    bucket_width = max(1, min(100, bucket))

    # Pull the needed columns
    rows = db.execute(
        select(
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
        )
    ).all()

    total = len(rows)
    unscored_count = 0
    scored_scores: List[float] = []
    bucket_counts: Dict[int, int] = {}
    risk_tier_counts: Dict[str, int] = {}

    for trust_score, risk_tier in rows:
        # risk tier mix (ignore null tiers)
        if risk_tier is not None:
            risk_tier_counts[risk_tier] = risk_tier_counts.get(risk_tier, 0) + 1

        if trust_score is None:
            unscored_count += 1
            continue

        # collect for stats
        scored_scores.append(float(trust_score))

        # bucket calculation
        idx = int(min(99, max(0, trust_score)) // bucket_width)
        bucket_counts[idx] = bucket_counts.get(idx, 0) + 1

    scored_count = len(scored_scores)

    # aggregate stats over non‑null scores
    if scored_scores:
        min_score = min(scored_scores)
        max_score = max(scored_scores)
        mean_score = sum(scored_scores) / scored_count
    else:
        min_score = max_score = mean_score = None

    # build bucket list sorted by low bound
    buckets: List[Dict[str, int]] = []
    for idx in sorted(bucket_counts):
        low = idx * bucket_width
        high = low + bucket_width - 1
        buckets.append(
            {"bucket_low": low, "bucket_high": high, "count": bucket_counts[idx]}
        )

    # build risk tier mix list sorted by tier name
    risk_tier_mix = [
        {"risk_tier": tier, "count": cnt}
        for tier, cnt in sorted(risk_tier_counts.items())
    ]

    return {
        "bucket_width": bucket_width,
        "total": total,
        "stats": {
            "count": total,
            "scored_count": scored_count,
            "unscored_count": unscored_count,
            "min": min_score,
            "max": max_score,
            "mean": mean_score,
        },
        "buckets": buckets,
        "risk_tier_mix": risk_tier_mix,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Seed test data
    test_rows = [
        McpServerRegistry(
            server_id="s1",
            name="Server One",
            trust_score=5.0,
            risk_tier="LOW",
            registry_source="src",
            last_assessed=None,
        ),
        McpServerRegistry(
            server_id="s2",
            name="Server Two",
            trust_score=45.0,
            risk_tier="LOW",
            registry_source="src",
            last_assessed=None,
        ),
        McpServerRegistry(
            server_id="s3",
            name="Server Three",
            trust_score=92.0,
            risk_tier="HIGH",
            registry_source="src",
            last_assessed=None,
        ),
        McpServerRegistry(
            server_id="s4",
            name="Server Four",
            trust_score=None,
            risk_tier="MEDIUM",
            registry_source="src",
            last_assessed=None,
        ),
    ]
    db.add_all(test_rows)
    db.commit()

    # Execute logic
    result = trust_score_distribution(db, bucket=10)

    # Assertions per contract
    assert result["total"] == 4, "total count mismatch"
    stats = result["stats"]
    assert stats["scored_count"] == 3, "scored count mismatch"
    assert stats["unscored_count"] == 1, "unscored count mismatch"
    bucket_sum = sum(b["count"] for b in result["buckets"])
    assert bucket_sum == stats["scored_count"], "bucket sum mismatch"
    expected_mean = (5.0 + 45.0 + 92.0) / 3.0
    assert abs(stats["mean"] - expected_mean) < 1e-6, "mean mismatch"
    # risk tier mix should contain LOW with count 2
    low_entry = next((e for e in result["risk_tier_mix"] if e["risk_tier"] == "LOW"), None)
    assert low_entry is not None and low_entry["count"] == 2, "risk tier mix mismatch"

    print("PASS")