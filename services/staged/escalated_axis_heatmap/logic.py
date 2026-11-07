from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def compute_escalated_axis_heatmap(db: Session) -> Dict[str, Any]:
    """Compute escalated axis counts from real application data."""
    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count(McpLlmAxisScore.id),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.escalated_to)
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
        )
    ).all()

    axes: List[Dict[str, Any]] = []
    total_escalations = 0
    for axis_name, escalated_to, count in rows:
        count = int(count)
        total_escalations += count
        axes.append(
            {
                "axis_name": axis_name,
                "escalated_to": escalated_to,
                "count": count,
            }
        )

    return {
        "axes": axes,
        "total_escalations": total_escalations,
    }


def get_escalated_axis_heatmap(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """FastAPI-compatible entry point for the escalated-axis heatmap."""
    return compute_escalated_axis_heatmap(db)


# Keep common service naming conventions available to thin routers.
escalated_axis_heatmap = compute_escalated_axis_heatmap
get_axis_heatmap = get_escalated_axis_heatmap


def _self_test() -> None:
    """Exercise the real ORM models against a local SQLite database."""
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    session = SessionLocal()
    try:
        session.add_all(
            [
                McpServerRegistry(server_id="heatmap-server-1"),
                McpServerRegistry(server_id="heatmap-server-2"),
                McpServerRegistry(server_id="heatmap-server-3"),
            ]
        )
        session.flush()

        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="heatmap-server-1",
                    axis_name="overall_risk",
                    model_version="test-model",
                    escalated=True,
                    escalated_to="manual_review",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="heatmap-server-2",
                    axis_name="auth_strength",
                    model_version="test-model",
                    escalated=True,
                    escalated_to="policy_review",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="heatmap-server-3",
                    axis_name="overall_risk",
                    model_version="test-model",
                    escalated=True,
                    escalated_to="manual_review",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        local_session = SessionLocal()
        try:
            yield local_session
        finally:
            local_session.close()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/risk/escalated-axis-heatmap")
    def heatmap_endpoint(payload=Depends(get_escalated_axis_heatmap)):
        return payload

    with TestClient(local_app) as client:
        response = client.get("/api/risk/escalated-axis-heatmap")
        assert response.status_code == 200

    payload = response.json()
    assert payload["total_escalations"] == 3
    assert any(
        axis["axis_name"] == "overall_risk"
        and axis["escalated_to"] == "manual_review"
        and axis["count"] == 2
        for axis in payload["axes"]
    )
    assert any(
        axis["axis_name"] == "auth_strength"
        and axis["escalated_to"] == "policy_review"
        and axis["count"] == 1
        for axis in payload["axes"]
    )

    print("PASS")


if __name__ == "__main__":
    _self_test()