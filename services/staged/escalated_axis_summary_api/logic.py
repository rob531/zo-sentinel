"""logic.py -- data/computation for escalated_axis_summary_api.

Provides a pure function that aggregates escalated LLM axis scores over a
time window. Mirrors the exemplar pattern: imports the real app DB session and
models, performs ORM queries, and returns plain‑Python structures for the router.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# The seven legal axis names – the test data uses a subset of these.
LEGAL_AXES = {
    "axis1",
    "axis2",
    "axis3",
    "axis4",
    "axis5",
    "axis6",
    "axis7",
}


def escalated_axis_summary(days: int, db: Session) -> Dict[str, Any]:
    """Return escalation aggregation for the past *days*.

    The result matches the contract expected by the router:
    {
        "days": int,
        "escalations": [
            {
                "axis_name": str,
                "escalated_to": str,   # "none" if NULL
                "rows": int,
                "servers": int,
                "avg_p_critical": float,
                "avg_p_danger": float,
                "last_scored_at": str, # ISO‑8601
            },
            ...
        ],
        "top_servers": [
            {
                "server_id": int,
                "axis_name": str,
                "escalated_to": str,   # "none" if NULL
                "avg_p_critical": float,
            },
            ...
        ],
        "total_escalations": int,
    }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # ------------------------------------------------------------------ #
    # Aggregation per (axis_name, escalated_to)
    # ------------------------------------------------------------------ #
    agg_stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count().label("rows"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.escalated_to)
    )
    agg_rows = db.execute(agg_stmt).all()

    escalations: List[Dict[str, Any]] = []
    for axis_name, escalated_to, rows_cnt, servers_cnt, avg_crit, avg_dang, last_at in agg_rows:
        escalations.append(
            {
                "axis_name": axis_name,
                "escalated_to": escalated_to if escalated_to is not None else "none",
                "rows": rows_cnt,
                "servers": servers_cnt,
                "avg_p_critical": float(avg_crit) if avg_crit is not None else None,
                "avg_p_danger": float(avg_dang) if avg_dang is not None else None,
                "last_scored_at": last_at.isoformat() if last_at else None,
            }
        )

    # ------------------------------------------------------------------ #
    # Top escalated servers (by avg p_critical)
    # ------------------------------------------------------------------ #
    top_stmt = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
        )
        .order_by(func.avg(McpLlmAxisScore.p_critical).desc())
        .limit(20)
    )
    top_rows = db.execute(top_stmt).all()

    top_servers: List[Dict[str, Any]] = []
    for server_id, axis_name, escalated_to, avg_crit in top_rows:
        top_servers.append(
            {
                "server_id": server_id,
                "axis_name": axis_name,
                "escalated_to": escalated_to if escalated_to is not None else "none",
                "avg_p_critical": float(avg_crit) if avg_crit is not None else None,
            }
        )

    total_escalations = sum(item["rows"] for item in escalations)

    return {
        "days": days,
        "escalations": escalations,
        "top_servers": top_servers,
        "total_escalations": total_escalations,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test – overrides the real DB dependency.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    Base.metadata.create_all(engine)

    db: Session = SessionLocal()

    now = datetime.utcnow()

    # Seed a few escalated rows across two axes.
    db.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id=1,
                axis_name="axis1",
                label="lbl",
                escalated=True,
                escalated_to="tierA",
                p_critical=0.8,
                p_danger=0.2,
                scored_at=now - timedelta(days=1),
                model_version="v1",
            ),
            McpLlmAxisScore(
                id=2,
                server_id=2,
                axis_name="axis2",
                label="lbl",
                escalated=True,
                escalated_to=None,
                p_critical=0.6,
                p_danger=0.4,
                scored_at=now - timedelta(days=2),
                model_version="v1",
            ),
            # Non‑escalated row – should be ignored.
            McpLlmAxisScore(
                id=3,
                server_id=3,
                axis_name="axis1",
                label="lbl",
                escalated=False,
                escalated_to=None,
                p_critical=0.1,
                p_danger=0.9,
                scored_at=now - timedelta(days=3),
                model_version="v1",
            ),
        ]
    )
    db.commit()

    result = escalated_axis_summary(days=30, db=db)

    assert result["days"] == 30
    assert len(result["escalations"]) == 2
    assert result["total_escalations"] == 2
    for esc in result["escalations"]:
        assert esc["axis_name"] in LEGAL_AXES

    print("PASS")