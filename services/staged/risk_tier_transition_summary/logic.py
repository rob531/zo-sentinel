# services/staged/risk_tier_transition_summary/logic.py

from datetime import datetime, timedelta
from typing import List, Dict, Any

from fastapi import Depends
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import PerspectiveEvent, McpServerRegistry


def _compute_summary(days: int, session: Session) -> Dict[str, Any]:
    """Core implementation used by the public endpoint and the self‑test."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Count transitions grouped by old and new tier
    transition_counts_subq = (
        select(
            PerspectiveEvent.old_tier.label("from_tier"),
            PerspectiveEvent.new_tier.label("to_tier"),
            func.count().label("cnt"),
        )
        .where(
            PerspectiveEvent.created_at >= cutoff,
            PerspectiveEvent.old_tier.is_not(None),
            PerspectiveEvent.new_tier.is_not(None),
        )
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .subquery()
    )

    # Total number of transitions in the period
    total_transitions = (
        session.execute(select(func.coalesce(func.sum(transition_counts_subq.c.cnt), 0)))
        .scalar_one()
    )

    # Number of distinct servers that had at least one transition
    servers_ever_affected = (
        session.execute(
            select(func.count(func.distinct(PerspectiveEvent.server_id))).where(
                PerspectiveEvent.created_at >= cutoff
            )
        )
        .scalar_one()
    )

    # Build the per‑tier transition list
    rows = session.execute(
        select(
            transition_counts_subq.c.from_tier,
            transition_counts_subq.c.to_tier,
            transition_counts_subq.c.cnt,
        )
    ).all()

    transitions: List[Dict[str, Any]] = []
    for from_tier, to_tier, cnt in rows:
        pct = (cnt / total_transitions * 100) if total_transitions else 0.0
        transitions.append(
            {
                "from_tier": from_tier,
                "to_tier": to_tier,
                "count": cnt,
                "pct": round(pct, 2),
            }
        )

    return {
        "period_days": days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "transitions": transitions,
        "total_transitions": total_transitions,
        "servers_ever_affected": servers_ever_affected,
    }


def get_transition_summary(days: int = 30, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    FastAPI endpoint implementation.

    Returns a summary of risk‑tier transitions that occurred in the last ``days`` days.
    """
    return _compute_summary(days, session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables based on the real models
    Base.metadata.create_all(engine)

    # Seed data
    sess = SessionLocal()
    try:
        # Three servers
        servers = [
            McpServerRegistry(server_id="srv-1", name="srv-1", risk_tier="low"),
            McpServerRegistry(server_id="srv-2", name="srv-2", risk_tier="medium"),
            McpServerRegistry(server_id="srv-3", name="srv-3", risk_tier="high"),
        ]
        sess.add_all(servers)

        now = datetime.utcnow()
        # Five transitions across two days
        events = [
            PerspectiveEvent(
                server_id="srv-1",
                perspective_id=1,
                old_tier="low",
                new_tier="medium",
                created_at=now - timedelta(hours=1),
                change_type="risk_tier_change",
                seen=True,
            ),
            PerspectiveEvent(
                server_id="srv-2",
                perspective_id=1,
                old_tier="medium",
                new_tier="high",
                created_at=now - timedelta(hours=2),
                change_type="risk_tier_change",
                seen=True,
            ),
            PerspectiveEvent(
                server_id="srv-1",
                perspective_id=1,
                old_tier="medium",
                new_tier="high",
                created_at=now - timedelta(days=1, hours=3),
                change_type="risk_tier_change",
                seen=True,
            ),
            PerspectiveEvent(
                server_id="srv-3",
                perspective_id=1,
                old_tier="high",
                new_tier="medium",
                created_at=now - timedelta(days=1, hours=4),
                change_type="risk_tier_change",
                seen=True,
            ),
            PerspectiveEvent(
                server_id="srv-2",
                perspective_id=1,
                old_tier="high",
                new_tier="low",
                created_at=now - timedelta(days=1, hours=5),
                change_type="risk_tier_change",
                seen=True,
            ),
        ]
        sess.add_all(events)
        sess.commit()

        # Run the summary logic
        result = _compute_summary(days=30, session=sess)

        # Assertions required by the acceptance criteria
        assert result["total_transitions"] == 5, "expected 5 total transitions"
        assert isinstance(result["transitions"], list) and len(result["transitions"]) >= 1, (
            "expected at least one transition entry"
        )
        print("PASS")
    finally:
        sess.close()