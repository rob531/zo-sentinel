"""logic.py -- data/computation for the app3t_axis_ptop_heatmap_api service.

Provides a pure function that queries the real McpLlmAxisScore table via a
SQLAlchemy Session and returns a heat‑map aggregation of p_top values per
axis/label pair.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base  # real DB session factory and metadata
from app.models import McpLlmAxisScore

# ---------------------------------------------------------------------------

_ALLOWED_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def axis_ptop_heatmap(
    db: Session, axis_name: Optional[str] = None
) -> Dict[str, List[Dict]]:
    """
    Return a heat‑map of ``p_top`` statistics grouped by axis and label.

    The result shape matches the contract for ``GET /api/axes/heatmap``::

        {
            "axes": [
                {
                    "axis_name": "<axis>",
                    "labels": [
                        {
                            "label": "<label>",
                            "label_index": <int>,
                            "mean_p_top": <float>,
                            "max_p_top": <float>,
                            "n": <int>
                        },
                        ...
                    ],
                    "n": <int>               # total rows for this axis
                },
                ...
            ]
        }

    Parameters
    ----------
    db:
        An active SQLAlchemy Session.
    axis_name:
        Optional filter; must be one of the seven allowed axis names.
        If supplied and not allowed, a ``ValueError`` is raised.

    Returns
    -------
    dict
        The aggregation payload described above.
    """
    if axis_name is not None and axis_name not in _ALLOWED_AXES:
        raise ValueError(f"Invalid axis_name: {axis_name!r}")

    stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            func.avg(McpLlmAxisScore.p_top).label("mean_p_top"),
            func.max(McpLlmAxisScore.p_top).label("max_p_top"),
            func.count().label("n"),
        )
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
    )
    if axis_name is not None:
        stmt = stmt.where(McpLlmAxisScore.axis_name == axis_name)

    rows = db.execute(stmt).all()

    # Accumulate per‑axis structures
    axis_map: Dict[str, Dict] = {}
    for (
        ax,
        lbl,
        lbl_idx,
        mean_ptop,
        max_ptop,
        cnt,
    ) in rows:
        if ax not in axis_map:
            axis_map[ax] = {"axis_name": ax, "labels": [], "n": 0}
        axis_entry = axis_map[ax]
        axis_entry["labels"].append(
            {
                "label": lbl,
                "label_index": int(lbl_idx) if lbl_idx is not None else None,
                "mean_p_top": float(mean_ptop),
                "max_p_top": float(max_ptop),
                "n": int(cnt),
            }
        )
        axis_entry["n"] += int(cnt)

    return {"axes": list(axis_map.values())}


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Self‑test that seeds an in‑memory SQLite DB with three rows and validates
    the contract expectations.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import datetime
    import json

    # In‑memory SQLite engine; use the same Base metadata as the real app.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Override the FastAPI dependency to use our in‑memory session.
    def _override_get_session() -> Session:
        return SessionLocal()

    # Seed data
    with SessionLocal() as sess:
        rows = [
            McpLlmAxisScore(
                id=1,
                server_id=101,
                axis_name="overall_risk",
                label="low",
                label_index=0,
                model_version="v1",
                probs=json.dumps({"low": 0.7, "high": 0.3}),
                p_top=0.7,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                escalated_to=None,
                scored_at=datetime.datetime.utcnow(),
            ),
            McpLlmAxisScore(
                id=2,
                server_id=102,
                axis_name="overall_risk",
                label="high",
                label_index=1,
                model_version="v1",
                probs=json.dumps({"low": 0.2, "high": 0.8}),
                p_top=0.8,
                p_critical=0.15,
                p_danger=0.05,
                escalated=False,
                escalated_to=None,
                scored_at=datetime.datetime.utcnow(),
            ),
            McpLlmAxisScore(
                id=3,
                server_id=103,
                axis_name="auth_strength",
                label="weak",
                label_index=0,
                model_version="v1",
                probs=json.dumps({"weak": 0.6, "strong": 0.4}),
                p_top=0.6,
                p_critical=0.2,
                p_danger=0.2,
                escalated=False,
                escalated_to=None,
                scored_at=datetime.datetime.utcnow(),
            ),
        ]
        sess.add_all(rows)
        sess.commit()

        # 1️⃣ Full heat‑map
        payload = axis_ptop_heatmap(sess)
        assert "axes" in payload
        assert len(payload["axes"]) == 2  # two distinct axes seeded
        total_n = sum(ax["n"] for ax in payload["axes"])
        assert total_n == 3

        # 2️⃣ Filtered by a valid axis
        filtered = axis_ptop_heatmap(sess, axis_name="overall_risk")
        assert len(filtered["axes"]) == 1
        assert filtered["axes"][0]["axis_name"] == "overall_risk"
        assert filtered["axes"][0]["n"] == 2

        # 3️⃣ Invalid axis raises
        try:
            axis_ptop_heatmap(sess, axis_name="invalid_axis")
            raise AssertionError("Expected ValueError for invalid axis_name")
        except ValueError:
            pass

    print("PASS")