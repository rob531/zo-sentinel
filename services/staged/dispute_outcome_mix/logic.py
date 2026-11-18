"""services/staged/dispute_outcome_mix/logic.py

Data/computation layer for the dispute outcome mix service.

Provides a single pure function that operates over a SQLAlchemy Session
and returns a dictionary summarising the distribution of dispute outcomes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute


def dispute_outcome_mix(db: Session) -> Dict[str, object]:
    """Return a summary of dispute outcomes.

    The result dictionary contains:
        - total: total number of disputes
        - by_status: mapping of status → count
        - by_reason_category: mapping of reason_category → count
        - by_proposed_tier: mapping of proposed_overall_risk → count
        - resolved_count: number of disputes with a non‑null resolved_at
        - open_count: total - resolved_count
        - resolution_rate: resolved_count / total (0.0 when total == 0)
        - median_resolution_hours: median of (resolved_at - created_at) in hours
          for resolved disputes, or ``None`` when no disputes are resolved.
    """
    # total count
    total: int = db.execute(
        select(func.count()).select_from(McpScoreDispute)
    ).scalar_one()

    # counts by status
    rows_status = db.execute(
        select(McpScoreDispute.status, func.count())
        .group_by(McpScoreDispute.status)
    ).all()
    by_status: Dict[str, int] = {
        (status or "unknown"): int(cnt) for status, cnt in rows_status
    }

    # counts by reason_category
    rows_reason = db.execute(
        select(McpScoreDispute.reason_category, func.count())
        .group_by(McpScoreDispute.reason_category)
    ).all()
    by_reason_category: Dict[str, int] = {
        (reason or "unknown"): int(cnt) for reason, cnt in rows_reason
    }

    # counts by proposed_overall_risk (tier)
    rows_tier = db.execute(
        select(McpScoreDispute.proposed_overall_risk, func.count())
        .group_by(McpScoreDispute.proposed_overall_risk)
    ).all()
    by_proposed_tier: Dict[str, int] = {
        (tier or "unknown"): int(cnt) for tier, cnt in rows_tier
    }

    # resolved vs open
    resolved_count: int = db.execute(
        select(func.count())
        .select_from(McpScoreDispute)
        .where(McpScoreDispute.resolved_at.is_not(None))
    ).scalar_one()
    open_count: int = total - resolved_count

    resolution_rate: float = (
        resolved_count / total if total else 0.0
    )

    # median resolution time in hours
    duration_rows: List[tuple[datetime, datetime]] = db.execute(
        select(McpScoreDispute.created_at, McpScoreDispute.resolved_at)
        .where(McpScoreDispute.resolved_at.is_not(None))
    ).all()
    durations_hours: List[float] = [
        (resolved - created).total_seconds() / 3600.0
        for created, resolved in duration_rows
    ]
    median_resolution_hours: Optional[float]
    if not durations_hours:
        median_resolution_hours = None
    else:
        durations_hours.sort()
        n = len(durations_hours)
        mid = n // 2
        if n % 2 == 1:
            median_resolution_hours = durations_hours[mid]
        else:
            median_resolution_hours = (durations_hours[mid - 1] + durations_hours[mid]) / 2.0

    return {
        "total": total,
        "by_status": by_status,
        "by_reason_category": by_reason_category,
        "by_proposed_tier": by_proposed_tier,
        "resolved_count": resolved_count,
        "open_count": open_count,
        "resolution_rate": resolution_rate,
        "median_resolution_hours": median_resolution_hours,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base

    # In‑memory SQLite engine (compatible with the app's declarative Base)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Seed data
    now = datetime.utcnow()
    disputes = [
        # Resolved disputes
        McpScoreDispute(
            id=1,
            server_id=101,
            proposed_overall_risk="high",
            reason_category="policy",
            status="resolved",
            explanation="explanation",
            submitted_by=1,
            created_at=now,
            resolved_at=now.replace(hour=now.hour + 2),
        ),
        McpScoreDispute(
            id=2,
            server_id=102,
            proposed_overall_risk="medium",
            reason_category="risk",
            status="resolved",
            explanation="explanation",
            submitted_by=2,
            created_at=now,
            resolved_at=now.replace(hour=now.hour + 5),
        ),
        McpScoreDispute(
            id=3,
            server_id=103,
            proposed_overall_risk="low",
            reason_category="policy",
            status="resolved",
            explanation="explanation",
            submitted_by=3,
            created_at=now,
            resolved_at=now.replace(hour=now.hour + 8),
        ),
        # Open disputes
        McpScoreDispute(
            id=4,
            server_id=104,
            proposed_overall_risk="high",
            reason_category="risk",
            status="open",
            explanation="explanation",
            submitted_by=4,
            created_at=now,
            resolved_at=None,
        ),
        McpScoreDispute(
            id=5,
            server_id=105,
            proposed_overall_risk="medium",
            reason_category="policy",
            status="open",
            explanation="explanation",
            submitted_by=5,
            created_at=now,
            resolved_at=None,
        ),
    ]

    with SessionLocal() as db:
        db.add_all(disputes)
        db.commit()

        result = dispute_outcome_mix(db)

    # Basic assertions matching the contract
    assert result["total"] == 5
    assert sum(result["by_status"].values()) == 5
    assert result["resolved_count"] == 3
    assert result["median_resolution_hours"] is not None

    print("PASS")