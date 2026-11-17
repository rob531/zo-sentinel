"""logic.py -- data/computation layer for axis_p_critical_buckets service.

Provides a function that aggregates the latest MCP LLM axis scores per
(server_id, axis_name) into p_critical buckets and per‑axis statistics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _bucket_label(p: float) -> str:
    """Return the bucket label for a given p_critical value."""
    bands = [
        (0.0, 0.01, "0.0-0.01"),
        (0.01, 0.05, "0.01-0.05"),
        (0.05, 0.20, "0.05-0.2"),
        (0.20, 1.0, "0.2-1.0"),
    ]
    for lo, hi, label in bands:
        if lo <= p < hi:
            return label
    # fallback (should not happen with valid data)
    return "unknown"


def p_critical_buckets(
    db: Session,
    axis_name: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Compute bucketed p_critical statistics.

    Returns a dict matching the contract:
    {
        "total_rows": int,
        "buckets": [{"band": str, "count": int}, ...],
        "by_axis": [
            {
                "axis_name": str,
                "count": int,
                "escalated_count": int,
                "mean_p_critical": float,
            },
            ...
        ],
        "escalated_total": int,
    }
    """
    # sub‑query: latest scored_at per (server_id, axis_name)
    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    )
    if axis_name:
        latest_subq = latest_subq.where(McpLlmAxisScore.axis_name == axis_name)

    latest_subq = latest_subq.subquery()

    # join back to get the full rows for those latest timestamps
    stmt = (
        select(McpLlmAxisScore)
        .join(
            latest_subq,
            and_(
                McpLlmAxisScore.server_id == latest_subq.c.server_id,
                McpLlmAxisScore.axis_name == latest_subq.c.axis_name,
                McpLlmAxisScore.scored_at == latest_subq.c.max_scored_at,
            ),
        )
    )
    if limit:
        stmt = stmt.limit(limit)

    rows: List[McpLlmAxisScore] = db.execute(stmt).scalars().all()

    total_rows = len(rows)

    # bucket aggregation
    bucket_counts: Dict[str, int] = defaultdict(int)
    escalated_total = 0

    # per‑axis aggregation
    axis_stats: Dict[
        str,
        Dict[str, Any],
    ] = defaultdict(lambda: {"count": 0, "escalated": 0, "p_sum": 0.0})

    for row in rows:
        p_val = float(row.p_critical) if row.p_critical is not None else 0.0
        bucket = _bucket_label(p_val)
        bucket_counts[bucket] += 1

        if row.escalated:
            escalated_total += 1

        axis = row.axis_name or "unknown"
        stats = axis_stats[axis]
        stats["count"] += 1
        stats["p_sum"] += p_val
        if row.escalated:
            stats["escalated"] += 1

    # build response structures
    buckets_list = [
        {"band": band, "count": bucket_counts.get(band, 0)}
        for band in ["0.0-0.01", "0.01-0.05", "0.05-0.2", "0.2-1.0"]
    ]

    by_axis_list = []
    for axis, stats in axis_stats.items():
        count = stats["count"]
        mean_p = stats["p_sum"] / count if count else 0.0
        by_axis_list.append(
            {
                "axis_name": axis,
                "count": count,
                "escalated_count": stats["escalated"],
                "mean_p_critical": mean_p,
            }
        )

    return {
        "total_rows": total_rows,
        "buckets": buckets_list,
        "by_axis": by_axis_list,
        "escalated_total": escalated_total,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the model we use
    McpLlmAxisScore.__table__.metadata.create_all(engine)

    # Seed data
    now = datetime.utcnow()
    test_rows = [
        # axis1
        McpLlmAxisScore(
            id=1,
            server_id=1,
            axis_name="axis1",
            model_version="v1",
            p_critical=0.005,
            escalated=False,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            server_id=2,
            axis_name="axis1",
            model_version="v1",
            p_critical=0.03,
            escalated=False,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3,
            server_id=3,
            axis_name="axis1",
            model_version="v1",
            p_critical=0.10,
            escalated=False,
            scored_at=now,
        ),
        # axis2
        McpLlmAxisScore(
            id=4,
            server_id=4,
            axis_name="axis2",
            model_version="v1",
            p_critical=0.50,
            escalated=False,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=5,
            server_id=5,
            axis_name="axis2",
            model_version="v1",
            p_critical=0.80,
            escalated=False,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=6,
            server_id=6,
            axis_name="axis2",
            model_version="v1",
            p_critical=0.02,
            escalated=True,
            escalated_to="team-x",
            scored_at=now,
        ),
    ]

    sess = SessionLocal()
    sess.add_all(test_rows)
    sess.commit()

    result = p_critical_buckets(sess)

    assert result["total_rows"] == 6, "total_rows mismatch"
    bucket_sum = sum(b["count"] for b in result["buckets"])
    assert bucket_sum == 6, "bucket counts do not sum to total rows"
    assert result["escalated_total"] == 1, "escalated_total mismatch"

    print("PASS")