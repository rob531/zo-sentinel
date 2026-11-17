"""Logic for the axis label catalog service.

Reads the authoritative application data through the real SQLAlchemy models and
returns the canonical axis catalog without performing writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_axis_catalog(db: Session) -> Dict[str, object]:
    """Build the catalog from scored rows in the real application store."""
    aggregate_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("scored_rows"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.sum(
                case(
                    (McpLlmAxisScore.escalated.is_(True), 1),
                    else_=0,
                )
            ).label("escalated_rows"),
        )
        .where(McpLlmAxisScore.axis_name.in_(CANONICAL_AXIS_NAMES))
        .group_by(McpLlmAxisScore.axis_name)
    ).all()

    latest_rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .where(McpLlmAxisScore.axis_name.in_(CANONICAL_AXIS_NAMES))
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    ).all()

    latest_by_axis: Dict[str, object] = {}
    for axis_name, label, label_index in latest_rows:
        if axis_name not in latest_by_axis:
            latest_by_axis[axis_name] = (label, label_index)

    aggregates = {
        axis_name: {
            "scored_rows": int(scored_rows or 0),
            "last_scored_at": last_scored_at,
            "escalated_rows": int(escalated_rows or 0),
        }
        for axis_name, scored_rows, last_scored_at, escalated_rows in aggregate_rows
    }

    axes: List[Dict[str, object]] = []
    for axis_name in CANONICAL_AXIS_NAMES:
        aggregate = aggregates.get(axis_name)
        if aggregate is None:
            continue
        label, label_index = latest_by_axis.get(axis_name, (None, None))
        axes.append(
            {
                "axis_name": axis_name,
                "label": label,
                "label_index": label_index,
                **aggregate,
            }
        )

    return {
        "axes": axes,
        "generated_at": datetime.now(timezone.utc),
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpLlmAxisScore.__table__.create(engine)

    seeded_axes = ("overall_risk", "auth_strength")
    labels = {"overall_risk": ("High", 3), "auth_strength": ("Medium", 2)}
    rows = []
    row_id = 1
    for server_number in range(3):
        for axis_name in seeded_axes:
            label, label_index = labels[axis_name]
            rows.append(
                McpLlmAxisScore(
                    id=row_id,
                    server_id=server_number + 1,
                    axis_name=axis_name,
                    model_version="self-test",
                    label=label,
                    label_index=label_index,
                    escalated=(row_id % 2 == 0),
                    scored_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                )
            )
            row_id += 1

    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/axis/catalog")
    def axis_catalog(db: Session = Depends(get_session)):
        return get_axis_catalog(db)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/catalog")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == len(set(seeded_axes))
        assert sum(axis["escalated_rows"] for axis in payload["axes"]) == 3

    print("PASS")