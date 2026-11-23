"""logic.py -- data/computation layer for the axis_escalation_target_index service.

Provides a single pure function that aggregates escalated LLM axis scores.
All data access is performed via the real SQLAlchemy models from the app.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def axis_escalation_target_index(db: Session) -> Dict[str, Any]:
    """Aggregate escalated axis scores.

    Returns a dict with:
        - targets: mapping of ``escalated_to`` → count of escalations.
        - axes: list of per‑axis aggregation dicts.
        - total_escalations: total number of escalated rows.
        - servers_escalated: distinct server count with escalations.
        - generated_at: ISO‑8601 UTC timestamp of generation.
    """
    # Base filter: only escalated rows
    escalated_filter = McpLlmAxisScore.escalated.is_(True)

    # Total escalations
    total_escalations: int = db.scalar(
        select(func.count()).select_from(McpLlmAxisScore).where(escalated_filter)
    ) or 0

    # Distinct servers escalated
    servers_escalated: int = db.scalar(
        select(func.count(func.distinct(McpLlmAxisScore.server_id))).where(escalated_filter)
    ) or 0

    # Targets aggregation
    target_rows = db.execute(
        select(McpLlmAxisScore.escalated_to, func.count())
        .where(escalated_filter)
        .group_by(McpLlmAxisScore.escalated_to)
    ).all()
    targets: Dict[str, int] = {}
    for escalated_to, cnt in target_rows:
        # ``escalated_to`` may be NULL; skip such entries
        if escalated_to is not None:
            targets[escalated_to] = int(cnt)

    # Per‑axis aggregation (counts, avg p_critical, latest scored_at)
    axis_base_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count().label("escalated_count"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.max(McpLlmAxisScore.scored_at).label("latest_escalation_at"),
        )
        .where(escalated_filter)
        .group_by(McpLlmAxisScore.axis_name)
    ).all()

    axes: List[Dict[str, Any]] = []
    for axis_name, escalated_count, avg_pc, latest_at in axis_base_rows:
        # Determine top escalated_to for this axis
        top_row = db.execute(
            select(McpLlmAxisScore.escalated_to, func.count())
            .where(escalated_filter, McpLlmAxisScore.axis_name == axis_name)
            .group_by(McpLlmAxisScore.escalated_to)
            .order_by(func.count().desc())
            .limit(1)
        ).first()
        top_escalated_to = top_row[0] if top_row else None

        avg_pc_rounded = round(float(avg_pc), 4) if avg_pc is not None else None
        latest_iso = (
            latest_at.replace(tzinfo=timezone.utc).isoformat()
            if latest_at is not None
            else None
        )

        axes.append(
            {
                "axis_name": axis_name,
                "escalated_count": int(escalated_count),
                "top_escalated_to": top_escalated_to,
                "avg_p_critical": avg_pc_rounded,
                "latest_escalation_at": latest_iso,
            }
        )

    return {
        "targets": targets,
        "axes": axes,
        "total_escalations": total_escalations,
        "servers_escalated": servers_escalated,
        "generated_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing – overrides the real DB dependency
    engine = create_engine("sqlite:///:memory:", future=True)
    from app.db import Base  # type: ignore

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    with SessionLocal() as db:
        # Seed 5 rows (3 escalated)
        rows = [
            McpLlmAxisScore(
                id=1,
                server_id=1,
                axis_name="cpu",
                model_version="v1",
                escalated=True,
                escalated_to="teamA",
                p_critical=0.8,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id=2,
                axis_name="cpu",
                model_version="v1",
                escalated=True,
                escalated_to="teamA",
                p_critical=0.6,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=3,
                server_id=3,
                axis_name="memory",
                model_version="v1",
                escalated=True,
                escalated_to="teamB",
                p_critical=0.9,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=4,
                server_id=4,
                axis_name="disk",
                model_version="v1",
                escalated=False,
                escalated_to=None,
                p_critical=0.5,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=5,
                server_id=5,
                axis_name="cpu",
                model_version="v1",
                escalated=False,
                escalated_to=None,
                p_critical=None,
                scored_at=now,
            ),
        ]
        db.add_all(rows)
        db.commit()

        result = axis_escalation_target_index(db)

        # Assertions per acceptance criteria
        assert result["total_escalations"] == 3, "total_escalations mismatch"
        assert result["servers_escalated"] == 3, "servers_escalated mismatch"
        assert result["targets"] == {"teamA": 2, "teamB": 1}, "targets count mismatch"

        # Verify per‑axis data
        axis_map = {a["axis_name"]: a for a in result["axes"]}

        cpu = axis_map.get("cpu")
        assert cpu is not None, "cpu axis missing"
        assert cpu["escalated_count"] == 2, "cpu escalated_count mismatch"
        assert cpu["top_escalated_to"] == "teamA", "cpu top_escalated_to mismatch"
        assert cpu["avg_p_critical"] == 0.7, "cpu avg_p_critical mismatch"

        memory = axis_map.get("memory")
        assert memory is not None, "memory axis missing"
        assert memory["escalated_count"] == 1, "memory escalated_count mismatch"
        assert memory["top_escalated_to"] == "teamB", "memory top_escalated_to mismatch"
        assert memory["avg_p_critical"] == 0.9, "memory avg_p_critical mismatch"

        print("PASS")