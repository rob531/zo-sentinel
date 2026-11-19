"""services/staged/axis_escalation_watch_api/logic.py

Data/computation layer for the Axis Escalation Watch API. Provides a single
function that queries real application tables (via the app's SQLAlchemy
session) and returns a structured result suitable for the FastAPI router.
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

ALLOWED_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def fetch_escalations(
    db: Session,
    *,
    days: int = 30,
    min_p_top: float = 0.0,
) -> Dict:
    """
    Return escalated LLM axis scores for the last ``days`` days.

    The result dictionary matches the contract expected by the router:

    {
        "days": int,
        "min_p_top": float,
        "generated_at": datetime,
        "escalations": [
            {
                "server_id": str,
                "server_name": str,
                "risk_tier": str | None,
                "axis_name": str,
                "label": str | None,
                "p_top": float | None,
                "p_critical": float | None,
                "escalated_to": str | None,
                "scored_at": datetime,
                "decision_rule_version": str | None,
            },
            ...
        ],
        "axis_counts": {axis_name: int, ...},
        "total": int,
    }

    Only rows where ``McpLlmAxisScore.escalated`` is true, the axis name is one
    of the seven legal values, and ``p_top`` meets the ``min_p_top`` threshold
    are considered. Results are ordered by ``p_critical`` descending,
    truncated to the first 200 rows.
    """
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=days)

    # Base query with join to server registry
    stmt = (
        select(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.decision_rule_version,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            and_(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.scored_at >= cutoff,
                McpLlmAxisScore.p_top >= min_p_top,
                McpLlmAxisScore.axis_name.in_(ALLOWED_AXES),
            )
        )
        .order_by(McpLlmAxisScore.p_critical.desc())
        .limit(200)
    )

    rows = db.execute(stmt).all()

    escalations: List[Dict] = []
    axis_counts: Dict[str, int] = {axis: 0 for axis in ALLOWED_AXES}

    for (
        server_id,
        server_name,
        risk_tier,
        axis_name,
        label,
        p_top,
        p_critical,
        escalated_to,
        scored_at,
        decision_rule_version,
    ) in rows:
        escalations.append(
            {
                "server_id": server_id,
                "server_name": server_name,
                "risk_tier": risk_tier,
                "axis_name": axis_name,
                "label": label,
                "p_top": float(p_top) if p_top is not None else None,
                "p_critical": float(p_critical) if p_critical is not None else None,
                "escalated_to": escalated_to,
                "scored_at": scored_at,
                "decision_rule_version": decision_rule_version,
            }
        )
        axis_counts[axis_name] = axis_counts.get(axis_name, 0) + 1

    return {
        "days": days,
        "min_p_top": min_p_top,
        "generated_at": _dt.datetime.utcnow(),
        "escalations": escalations,
        "axis_counts": {k: v for k, v in axis_counts.items() if v > 0},
        "total": len(escalations),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Local in‑memory SQLite for the self‑test; overrides the real get_session.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build a temporary engine mirroring the real metadata.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create tables for the models we use.
    McpServerRegistry.metadata.create_all(engine)
    McpLlmAxisScore.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Seed data: three servers.
    servers = [
        McpServerRegistry(
            server_id="srv-1",
            name="alpha",
            risk_tier="high",
            verdict="malicious",
        ),
        McpServerRegistry(
            server_id="srv-2",
            name="beta",
            risk_tier="medium",
            verdict="suspicious",
        ),
        McpServerRegistry(
            server_id="srv-3",
            name="gamma",
            risk_tier="low",
            verdict="benign",
        ),
    ]
    db.add_all(servers)

    now = _dt.datetime.utcnow()

    # Seed axis scores: two escalated rows across two axes, plus a non‑escalated row.
    scores = [
        McpLlmAxisScore(
            id=1,
            server_id="srv-1",
            axis_name="overall_risk",
            label="critical",
            label_index=0,
            p_top=0.95,
            p_critical=0.99,
            escalated=True,
            escalated_to="critical",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now - _dt.timedelta(days=1),
        ),
        McpLlmAxisScore(
            id=2,
            server_id="srv-2",
            axis_name="auth_strength",
            label="danger",
            label_index=1,
            p_top=0.90,
            p_critical=0.85,
            escalated=True,
            escalated_to="danger",
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now - _dt.timedelta(days=2),
        ),
        McpLlmAxisScore(
            id=3,
            server_id="srv-3",
            axis_name="overall_risk",
            label="low",
            label_index=2,
            p_top=0.30,
            p_critical=0.10,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="m1",
            scored_at=now - _dt.timedelta(days=3),
        ),
    ]
    db.add_all(scores)
    db.commit()

    # Execute the core logic.
    result = fetch_escalations(db, days=30, min_p_top=0.0)

    # Assertions per contract.
    assert result["total"] == 2, "expected exactly two escalated rows"
    assert set(result["axis_counts"]).issubset(ALLOWED_AXES), "unexpected axis name in counts"
    esc = result["escalations"]
    assert esc[0]["p_critical"] >= esc[-1]["p_critical"], "escalations not ordered by p_critical desc"

    print("PASS")