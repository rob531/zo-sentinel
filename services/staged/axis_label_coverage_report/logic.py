"""services/staged/axis_label_coverage_report/logic.py

Data/computation layer for the *axis_label_coverage_report* service.

Provides a single pure function that, given a SQLAlchemy Session, returns a
dictionary matching the contract required by the router and the self‑test.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpLlmAxisScore

# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
VALID_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def axis_label_coverage(
    db: Session, *, days: int = 30, limit_axes: int = 20
) -> Dict:
    """
    Return label‑coverage statistics for each risk axis.

    The result shape is::

        {
            "days": <int>,
            "generated_at": "<ISO‑8601 UTC timestamp>",
            "axes": [
                {
                    "axis_name": "<axis>",
                    "score_count": <int>,
                    "labelled_count": <int>,
                    "unlabelled_count": <int>,
                    "coverage_pct": <float>,          # rounded to 1 dp
                    "labels": [
                        {"label": "<label>", "count": <int>},
                        ...
                    ],
                },
                ...
            ],
        }

    * ``days`` – look‑back window applied to ``scored_at``.
    * ``limit_axes`` – maximum number of axes to return after sorting.
      The contract requires that all seven axes are always present, so the
      limit is applied only after the mandatory axes have been added.
    """
    cutoff: datetime = datetime.utcnow() - timedelta(days=days)

    # ------------------------------------------------------------------- #
    # Gather raw rows filtered by the time window.
    # ------------------------------------------------------------------- #
    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    # ------------------------------------------------------------------- #
    # Initialise aggregation containers for every valid axis.
    # ------------------------------------------------------------------- #
    agg: Dict[str, Dict] = {
        axis: {
            "axis_name": axis,
            "score_count": 0,
            "labelled_count": 0,
            "unlabelled_count": 0,
            "labels": {},  # label -> count
        }
        for axis in VALID_AXES
    }

    # ------------------------------------------------------------------- #
    # Populate the containers.
    # ------------------------------------------------------------------- #
    for axis_name, label in rows:
        if axis_name not in agg:
            # ignore any stray axis names that are not part of the spec
            continue
        axis_data = agg[axis_name]
        axis_data["score_count"] += 1
        if label is None:
            axis_data["unlabelled_count"] += 1
        else:
            axis_data["labelled_count"] += 1
            axis_data["labels"][label] = axis_data["labels"].get(label, 0) + 1

    # ------------------------------------------------------------------- #
    # Build the final list, compute coverage_pct and sort.
    # ------------------------------------------------------------------- #
    axes_list: List[Dict] = []
    for axis_data in agg.values():
        total = axis_data["score_count"]
        labelled = axis_data["labelled_count"]
        coverage = (
            round(labelled / total * 100, 1) if total > 0 else 0.0
        )
        # transform label dict into list of dicts sorted by count desc
        label_items = [
            {"label": lbl, "count": cnt}
            for lbl, cnt in sorted(
                axis_data["labels"].items(),
                key=lambda i: (-i[1], i[0]),
            )
        ]
        axes_list.append(
            {
                "axis_name": axis_data["axis_name"],
                "score_count": total,
                "labelled_count": labelled,
                "unlabelled_count": axis_data["unlabelled_count"],
                "coverage_pct": coverage,
                "labels": label_items,
            }
        )

    # sort by unlabelled_count desc then axis_name asc
    axes_list.sort(
        key=lambda a: (-a["unlabelled_count"], a["axis_name"])
    )

    # enforce limit_axes after the mandatory 7‑axis guarantee
    if limit_axes < len(axes_list):
        axes_list = axes_list[:limit_axes]

    return {
        "days": days,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "axes": axes_list,
    }


# --------------------------------------------------------------------------- #
# FastAPI wiring (used by the self‑test)
# --------------------------------------------------------------------------- #
def _create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/axes/label-coverage")
    def endpoint(
        days: int = 30,
        limit_axes: int = 20,
        db: Session = Depends(get_session),
    ):
        return axis_label_coverage(db, days=days, limit_axes=limit_axes)

    return app


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine compatible with the app's declarative Base
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override for the FastAPI app used in the test
    def get_test_session() -> Session:  # pragma: no cover
        with TestSession() as s:
            yield s

    app = _create_app()
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed data: at least three axes, one with a NULL label.
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    seed_rows = [
        # Axis with two labelled scores
        McpLlmAxisScore(
            id=1,
            server_id=1,
            axis_name="overall_risk",
            label="high",
            label_index=0,
            model_version="v1",
            p_top=0.9,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            server_id=2,
            axis_name="overall_risk",
            label="medium",
            label_index=1,
            model_version="v1",
            p_top=0.7,
            scored_at=now,
        ),
        # Axis with one unlabelled score (NULL label)
        McpLlmAxisScore(
            id=3,
            server_id=3,
            axis_name="auth_strength",
            label=None,
            label_index=None,
            model_version="v1",
            p_top=0.5,
            scored_at=now,
        ),
        # Axis with a single labelled score
        McpLlmAxisScore(
            id=4,
            server_id=4,
            axis_name="network_egress",
            label="low",
            label_index=2,
            model_version="v1",
            p_top=0.3,
            scored_at=now,
        ),
    ]

    with TestSession() as sess:
        sess.add_all(seed_rows)
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/axes/label-coverage")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()

    # ---- contract assertions ------------------------------------------------
    assert isinstance(data, dict), "response is not a dict"
    assert data.get("days") == 30, "default days not reflected"
    axes = data.get("axes")
    assert isinstance(axes, list) and len(axes) == 7, "exactly 7 axes required"

    # Find the axis we seeded with a NULL label
    auth_axis = next(a for a in axes if a["axis_name"] == "auth_strength")
    assert auth_axis["unlabelled_count"] == 1, "NULL label not counted"
    assert auth_axis["coverage_pct"] < 100, "coverage_pct should be < 100"
    assert (
        auth_axis["labelled_count"] + auth_axis["unlabelled_count"]
        == auth_axis["score_count"]
    ), "label counts do not sum to score_count"

    print("PASS")