from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_axis_escalations(
    db: Session,
    days: int = 30,
    axis: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return escalated axis assessments within the requested review window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
    )

    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    rows = db.execute(statement).all()
    return [
        {
            "server_id": score.server_id,
            "server_name": registry.name,
            "axis_name": score.axis_name,
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "escalated_to": score.escalated_to,
            "risk_tier": registry.risk_tier,
            "decision_rule_version": score.decision_rule_version,
            "model_version": score.model_version,
            "scored_at": score.scored_at,
        }
        for score, registry in rows
    ]


# Keep conventional service-entry-point names available to thin routers.
list_axis_escalations = get_axis_escalations
get_escalations = get_axis_escalations
list_escalations = get_axis_escalations
get_axis_escalation_queue = get_axis_escalations


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    now = datetime.now(timezone.utc)

    session.add_all(
        [
            McpServerRegistry(
                server_id="server-1",
                name="First Server",
                risk_tier="high",
                verdict="review",
            ),
            McpServerRegistry(
                server_id="server-2",
                name="Second Server",
                risk_tier="medium",
                verdict="monitor",
            ),
            McpServerRegistry(
                server_id="server-3",
                name="Third Server",
                risk_tier="low",
                verdict="approved",
            ),
            McpLlmAxisScore(
                id=1,
                server_id="server-1",
                axis_name="privacy",
                label="privileged",
                p_top=0.91,
                p_critical=0.72,
                p_danger=0.81,
                escalated=True,
                escalated_to="security-review",
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now - timedelta(days=1),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-2",
                axis_name="privacy",
                label="ordinary",
                p_top=0.42,
                p_critical=0.11,
                p_danger=0.22,
                escalated=False,
                escalated_to=None,
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now - timedelta(days=2),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-3",
                axis_name="supply_chain",
                label="elevated",
                p_top=0.77,
                p_critical=0.43,
                p_danger=0.68,
                escalated=True,
                escalated_to="governance-review",
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now - timedelta(days=8),
            ),
            McpLlmAxisScore(
                id=4,
                server_id="server-2",
                axis_name="supply_chain",
                label="ordinary",
                p_top=0.25,
                p_critical=0.03,
                p_danger=0.14,
                escalated=False,
                escalated_to=None,
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now - timedelta(days=3),
            ),
        ]
    )
    session.commit()

    local_app = FastAPI()

    def override_get_session():
        yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/axes/escalations")
    def axis_escalations(
        days: int = 30,
        axis: Optional[str] = None,
        db: Session = Depends(get_session),
    ):
        escalations = get_axis_escalations(db, days=days, axis=axis)
        return {"days": days, "count": len(escalations), "escalations": escalations}

    response = TestClient(local_app).get(
        "/api/axes/escalations",
        params={"days": 7},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["escalations"][0]["axis_name"] == "privacy"
    assert payload["escalations"][0]["p_top"] == 0.91
    assert payload["escalations"][0]["escalated_to"] == "security-review"

    print("PASS")