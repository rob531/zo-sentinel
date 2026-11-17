from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_db_session(session: Session = Depends(get_session)) -> Session:
    return session


def _normalise_bucket_size(bucket_size: int) -> int:
    if not isinstance(bucket_size, int) or isinstance(bucket_size, bool):
        raise ValueError("bucket_size must be an integer")
    if bucket_size < 1 or bucket_size > 100:
        raise ValueError("bucket_size must be between 1 and 100")
    return bucket_size


def _bucket_boundaries(bucket_size: int) -> list[tuple[float, float]]:
    boundaries: list[tuple[float, float]] = []
    lower = 0
    while lower < 100:
        upper = min(lower + bucket_size, 100)
        boundaries.append((float(lower), float(upper)))
        lower = upper
    return boundaries


def _score_bucket_index(score: float, bucket_size: int, bucket_count: int) -> int:
    if score <= 0:
        return 0
    if score >= 100:
        return bucket_count - 1
    return min(int(score // bucket_size), bucket_count - 1)


def get_trust_score_distribution(
    session: Session,
    source: Optional[str] = None,
    bucket_size: int = 10,
) -> Dict[str, Any]:
    """Return a trust-score histogram and risk-tier counts for the registry."""
    bucket_size = _normalise_bucket_size(bucket_size)
    boundaries = _bucket_boundaries(bucket_size)

    statement = select(
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
    )
    if source is not None and source != "":
        statement = statement.where(McpServerRegistry.registry_source == source)

    rows = session.execute(statement).all()

    counts = [0] * len(boundaries)
    tier_counts: Dict[str, int] = {}
    total = 0
    scored = 0

    for row in rows:
        total += 1

        tier = row[1]
        tier_name = str(tier) if tier is not None else "unknown"
        tier_counts[tier_name] = tier_counts.get(tier_name, 0) + 1

        score = row[0]
        if score is None:
            continue

        scored += 1
        numeric_score = float(score)
        index = _score_bucket_index(numeric_score, bucket_size, len(boundaries))
        counts[index] += 1

    buckets = [
        {
            "lower": lower,
            "upper": upper,
            "count": counts[index],
        }
        for index, (lower, upper) in enumerate(boundaries)
    ]

    return {
        "bucket_size": bucket_size,
        "total": total,
        "scored": scored,
        "unscored": total - scored,
        "buckets": buckets,
        "by_tier": [
            {"risk_tier": tier, "count": tier_counts[tier]}
            for tier in sorted(tier_counts)
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_trust_distribution(
    session: Session,
    source: Optional[str] = None,
    bucket_size: int = 10,
) -> Dict[str, Any]:
    return get_trust_score_distribution(session, source, bucket_size)


def compute_trust_score_distribution(
    session: Session,
    source: Optional[str] = None,
    bucket_size: int = 10,
) -> Dict[str, Any]:
    return get_trust_score_distribution(session, source, bucket_size)


def compute_distribution(
    session: Session,
    source: Optional[str] = None,
    bucket_size: int = 10,
) -> Dict[str, Any]:
    return get_trust_score_distribution(session, source, bucket_size)


__all__ = [
    "get_db_session",
    "get_trust_score_distribution",
    "get_trust_distribution",
    "compute_trust_score_distribution",
    "compute_distribution",
]


if __name__ == "__main__":
    boundaries = _bucket_boundaries(10)
    assert len(boundaries) == 10
    assert boundaries[0] == (0.0, 10.0)
    assert boundaries[-1] == (90.0, 100.0)
    assert _score_bucket_index(5.0, 10, len(boundaries)) == 0
    assert _score_bucket_index(42.0, 10, len(boundaries)) == 4
    assert _score_bucket_index(95.0, 10, len(boundaries)) == 9
    assert _score_bucket_index(100.0, 10, len(boundaries)) == 9
    print("PASS")