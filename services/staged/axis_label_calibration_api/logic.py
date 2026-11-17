from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def build_axis_label_calibration(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    axis_name = McpLlmAxisScore.axis_name
    label = func.coalesce(McpLlmAxisScore.label, "unknown")
    label_index = func.coalesce(McpLlmAxisScore.label_index, -1)
    count = func.count()
    axis_total = func.sum(count).over(partition_by=axis_name)
    escalated_count = func.sum(
        case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
    )

    rows = (
        session.query(
            axis_name.label("axis_name"),
            label.label("label"),
            label_index.label("label_index"),
            count.label("count"),
            (count * 1.0 / axis_total).label("share"),
            func.avg(McpLlmAxisScore.p_top).label("mean_p_top"),
            escalated_count.label("escalated_count"),
        )
        .group_by(axis_name, label, label_index)
        .order_by(axis_name, label_index)
        .all()
    )

    axes: list[dict[str, Any]] = []
    current_axis: str | None = None
    current_labels: list[dict[str, Any]] = []

    for row in rows:
        if current_axis is not None and row.axis_name != current_axis:
            axes.append(
                {"axis_name": current_axis, "labels": current_labels}
            )
            current_labels = []

        current_axis = row.axis_name
        current_labels.append(
            {
                "label": row.label,
                "label_index": int(row.label_index),
                "count": int(row.count),
                "share": float(row.share),
                "mean_p_top": (
                    float(row.mean_p_top)
                    if row.mean_p_top is not None
                    else None
                ),
                "escalated_count": int(row.escalated_count),
            }
        )

    if current_axis is not None:
        axes.append({"axis_name": current_axis, "labels": current_labels})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
    }


get_axis_label_calibration = build_axis_label_calibration
axis_label_calibration = build_axis_label_calibration


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="auth_strength",
                    label="strong",
                    label_index=0,
                    p_top=0.90,
                    escalated=False,
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="auth_strength",
                    label="weak",
                    label_index=1,
                    p_top=0.40,
                    escalated=True,
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="auth_strength",
                    label="strong",
                    label_index=0,
                    p_top=0.70,
                    escalated=False,
                    model_version="test-model",
                ),
            ]
        )
        session.commit()

    def override_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/axis/calibration")
    def axis_calibration(session: Session = Depends(get_session)):
        return build_axis_label_calibration(session)

    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/axis/calibration")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 1
        assert payload["axes"][0]["axis_name"] == "auth_strength"
        labels = payload["axes"][0]["labels"]
        assert sum(label["count"] for label in labels) == 3
        assert sum(label["escalated_count"] for label in labels) == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()