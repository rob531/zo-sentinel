"""logic.py -- data/computation for the axis_escalation_leaderboard service.

Provides a pure function that can be called directly in tests or via the
router.  All DB access is performed through the real SQLAlchemy models
(imported from app.models) and a Session supplied by the FastAPI dependency
(get_session).  No inline or stub models are defined.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Dict, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# ---------------------------------------------------------------------------


def axis_escalation_leaderboard(db: Session) -> Dict[str, Any]:
    """
    Build a leaderboard of escalated LLM axis scores.

    For each distinct combination of ``axis_name`` and ``escalated_to`` we
    count the number of *distinct* servers where ``escalated`` is true.
    The result is ordered by that count descending.

    For each combination we also include the top servers (by ``p_critical``
    descending) limited to three entries.  Each top‑server entry contains the
    server identifier, its registered name and the ``p_critical`` probability.

    Returns
    -------
    dict
        ``{
            "generated_at": "<ISO‑8601 timestamp>",
            "axes": [
                {
                    "axis_name": str,
                    "escalated_to": str,
                    "server_count": int,
                    "top_servers": [
                        {
                            "server_id": str,
                            "name": str,
                            "p_critical": float,
                        },
                        ...
                    ],
                },
                ...
            ],
        }```
    """
    # -----------------------------------------------------------------------
    # 1️⃣  Aggregate distinct server counts per (axis_name, escalated_to)
    # -----------------------------------------------------------------------
    agg_stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_cnt"),
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.escalated_to)
        .order_by(func.count(func.distinct(McpLlmAxisScore.server_id)).desc())
    )
    agg_rows = db.execute(agg_stmt).all()

    # -----------------------------------------------------------------------
    # 2️⃣  For each aggregated row, fetch the top servers
    # -----------------------------------------------------------------------
    axes: List[Dict[str, Any]] = []
    for axis_name, escalated_to, server_cnt in agg_rows:
        top_stmt = (
            select(
                McpLlmAxisScore.server_id,
                McpServerRegistry.name,
                McpLlmAxisScore.p_critical,
            )
            .join(
                McpServerRegistry,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .where(
                McpLlmAxisScore.axis_name == axis_name,
                McpLlmAxisScore.escalated_to == escalated_to,
                McpLlmAxisScore.escalated.is_(True),
            )
            .order_by(McpLlmAxisScore.p_critical.desc().nullslast())
            .limit(3)
        )
        top_rows = db.execute(top_stmt).all()
        top_servers = [
            {
                "server_id": srv_id,
                "name": name,
                "p_critical": float(p_crit) if p_crit is not None else None,
            }
            for srv_id, name, p_crit in top_rows
        ]

        axes.append(
            {
                "axis_name": axis_name,
                "escalated_to": escalated_to,
                "server_count": int(server_cnt),
                "top_servers": top_servers,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
    }


# ---------------------------------------------------------------------------
# Dependency wrapper for FastAPI routers (router.py will import this)
# ---------------------------------------------------------------------------

def get_axis_escalation_leaderboard(session: Session = get_session()) -> Dict[str, Any]:
    """FastAPI dependency that supplies the leaderboard."""
    return axis_escalation_leaderboard(session)


# ---------------------------------------------------------------------------
# __main__ self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, seeds it with a minimal
    # dataset and validates the contract described in the task.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # 1️⃣  Create in‑memory DB and tables
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # 2️⃣  Seed data
    with SessionLocal() as db:
        # Servers
        servers = [
            McpServerRegistry(server_id="srv1", name="Alpha", risk_tier="high"),
            McpServerRegistry(server_id="srv2", name="Beta", risk_tier="high"),
            McpServerRegistry(server_id="srv3", name="Gamma", risk_tier="medium"),
            McpServerRegistry(server_id="srv4", name="Delta", risk_tier="low"),
        ]
        db.add_all(servers)

        # Axis scores
        scores = [
            # Axis A – escalated to "high"
            McpLlmAxisScore(
                id=1,
                server_id="srv1",
                axis_name="A",
                label="label1",
                label_index=0,
                model_version="v1",
                p_critical=0.9,
                escalated=True,
                escalated_to="high",
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv2",
                axis_name="A",
                label="label2",
                label_index=1,
                model_version="v1",
                p_critical=0.85,
                escalated=True,
                escalated_to="high",
            ),
            # Axis B – escalated to "medium"
            McpLlmAxisScore(
                id=3,
                server_id="srv3",
                axis_name="B",
                label="label3",
                label_index=2,
                model_version="v1",
                p_critical=0.7,
                escalated=True,
                escalated_to="medium",
            ),
            # Axis C – not escalated (should be ignored)
            McpLlmAxisScore(
                id=4,
                server_id="srv4",
                axis_name="C",
                label="label4",
                label_index=3,
                model_version="v1",
                p_critical=0.4,
                escalated=False,
                escalated_to=None,
            ),
        ]
        db.add_all(scores)
        db.commit()

        # 3️⃣  Execute the function under test
        result = axis_escalation_leaderboard(db)

        # 4️⃣  Assertions per contract
        assert result["generated_at"], "generated_at missing"
        axes = result["axes"]
        assert len(axes) >= 2, f"expected at least 2 axes, got {len(axes)}"

        # Find the entry for axis A / high
        a_entry = next(
            (a for a in axes if a["axis_name"] == "A" and a["escalated_to"] == "high"),
            None,
        )
        assert a_entry is not None, "Axis A / high entry missing"
        assert a_entry["server_count"] == 2, f"expected 2, got {a_entry['server_count']}"

        print("PASS")