"""logic.py -- escalated_axis_summary service data/computation layer.

Provides a single pure function that queries real application tables
(mcp_llm_axis_scores joined to mcp_server_registry) and returns a JSON‑serialisable
structure for the /api/scores/escalations endpoint.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_escalated_axis_summary(
    db: Session,
    *,
    escalated_to: Optional[str] = None,
    axis_name: Optional[str] = None,
    since_days: int = 7,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return escalated axis scores with optional filtering and pagination.

    Args:
        db: SQLAlchemy session.
        escalated_to: Optional exact match on the ``escalated_to`` column.
        axis_name: Optional exact match on the ``axis_name`` column.
        since_days: Look back this many days (clamped 1‑365, default 7).
        limit: Maximum rows to return (default 200, max 1000).
        offset: Pagination offset (default 0).

    Returns:
        Mapping matching the contract specification.
    """
    # Clamp parameters
    since_days = max(1, min(365, since_days))
    limit = max(0, min(1000, limit))
    offset = max(0, offset)

    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=since_days)

    # Base filter: escalated == True and escalated_to is not null
    base_filter = [
        McpLlmAxisScore.escalated.is_(True),
        McpLlmAxisScore.escalated_to.is_not(None),
        McpLlmAxisScore.scored_at >= cutoff,
    ]

    if escalated_to is not None:
        base_filter.append(McpLlmAxisScore.escalated_to == escalated_to)
    if axis_name is not None:
        base_filter.append(McpLlmAxisScore.axis_name == axis_name)

    # Total count (ignoring pagination)
    total_stmt = (
        select(func.count())
        .select_from(McpLlmAxisScore)
        .where(*base_filter)
    )
    total = db.execute(total_stmt).scalar_one()

    # Main query with join to server registry
    main_stmt = (
        select(
            McpLlmAxisScore,
            McpServerRegistry.name,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(*base_filter)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(main_stmt).all()

    escalations: List[Dict[str, Any]] = []
    for score, server_name in rows:
        escalations.append(
            {
                "server_id": str(score.server_id),
                "name": server_name,
                "axis_name": score.axis_name,
                "label": score.label,
                "p_top": float(score.p_top) if score.p_top is not None else None,
                "p_critical": (
                    float(score.p_critical) if score.p_critical is not None else None
                ),
                "p_danger": float(score.p_danger) if score.p_danger is not None else None,
                "escalated_to": score.escalated_to,
                "decision_rule_version": score.decision_rule_version,
                "model_version": score.model_version,
                "scored_at": score.scored_at.isoformat(),
            }
        )

    # Aggregation by axis (over the filtered set, not paginated)
    by_axis_stmt = (
        select(McpLlmAxisScore.axis_name, func.count())
        .where(*base_filter)
        .group_by(McpLlmAxisScore.axis_name)
    )
    axis_rows = db.execute(by_axis_stmt).all()
    by_axis = [{"axis_name": ax, "count": int(cnt)} for ax, cnt in axis_rows]

    return {
        "since_days": since_days,
        "total": int(total),
        "escalations": escalations,
        "by_axis": by_axis,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for isolated testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import Base from the app package and create tables
    from app.db import Base  # type: ignore

    Base.metadata.create_all(engine)

    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed data
    now = _dt.datetime.utcnow()
    with Session(engine) as sess:
        # Servers
        srv1 = McpServerRegistry(
            server_id="srv1",
            name="Server One",
            risk_tier="high",
        )
        srv2 = McpServerRegistry(
            server_id="srv2",
            name="Server Two",
            risk_tier="medium",
        )
        sess.add_all([srv1, srv2])

        # Axis scores
        esc1 = McpLlmAxisScore(
            id=1,
            server_id="srv1",
            axis_name="availability",
            label="high",
            label_index=0,
            p_top=0.9,
            p_critical=0.7,
            p_danger=0.5,
            escalated=True,
            escalated_to="tier2",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now,
        )
        esc2 = McpLlmAxisScore(
            id=2,
            server_id="srv2",
            axis_name="confidentiality",
            label="medium",
            label_index=1,
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.4,
            escalated=True,
            escalated_to="tier3",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now - _dt.timedelta(days=1),
        )
        non_esc = McpLlmAxisScore(
            id=3,
            server_id="srv1",
            axis_name="integrity",
            label="low",
            label_index=2,
            p_top=0.3,
            p_critical=0.2,
            p_danger=0.1,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now - _dt.timedelta(days=2),
        )
        sess.add_all([esc1, esc2, non_esc])
        sess.commit()

        # Run the core function
        result = get_escalated_axis_summary(sess)

        assert result["total"] == 2, "expected total 2"
        assert len(result["escalations"]) == 2, "expected 2 escalations"
        # by_axis should contain both axes with count 1 each
        axis_counts = {item["axis_name"]: item["count"] for item in result["by_axis"]}
        assert axis_counts.get("availability") == 1, "availability count mismatch"
        assert axis_counts.get("confidentiality") == 1, "confidentiality count mismatch"

        # Filter by axis_name
        filtered = get_escalated_axis_summary(sess, axis_name="availability")
        assert filtered["total"] == 1, "axis filter did not narrow result"
        assert filtered["escalations"][0]["axis_name"] == "availability"

        print("PASS")