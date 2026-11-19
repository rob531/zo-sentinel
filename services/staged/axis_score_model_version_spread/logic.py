"""Logic for the axis score model/version spread service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_model_version_distribution(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return axis-score activity grouped by model and decision-rule version."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    model_version = func.coalesce(McpLlmAxisScore.model_version, "unknown")

    server_count = func.count(func.distinct(McpLlmAxisScore.server_id))
    axis_count = func.count(func.distinct(McpLlmAxisScore.axis_name))
    escalated_rows = func.sum(
        case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
    )

    group_rows = db.execute(
        select(
            model_version.label("model_version"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            server_count.label("server_count"),
            axis_count.label("axis_count"),
            escalated_rows.label("escalated_rows"),
            func.avg(McpLlmAxisScore.p_top).label("p_top_avg"),
            func.max(McpLlmAxisScore.p_top).label("p_top_max"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(model_version, McpLlmAxisScore.decision_rule_version)
        .order_by(server_count.desc())
    ).all()

    summary = db.execute(
        select(
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers_scored"),
            func.count().label("rows_scored"),
            func.count(func.distinct(model_version)).label("distinct_model_versions"),
            func.count(
                func.distinct(McpLlmAxisScore.decision_rule_version)
            ).label("distinct_rule_versions"),
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).one()

    groups = []
    for row in group_rows:
        groups.append(
            {
                "model_version": row.model_version,
                "decision_rule_version": row.decision_rule_version,
                "server_count": int(row.server_count or 0),
                "axis_count": int(row.axis_count or 0),
                "escalated_rows": int(row.escalated_rows or 0),
                "p_top_avg": (
                    float(row.p_top_avg) if row.p_top_avg is not None else None
                ),
                "p_top_max": (
                    float(row.p_top_max) if row.p_top_max is not None else None
                ),
            }
        )

    return {
        "servers_scored": int(summary.servers_scored or 0),
        "rows_scored": int(summary.rows_scored or 0),
        "distinct_model_versions": int(summary.distinct_model_versions or 0),
        "distinct_rule_versions": int(summary.distinct_rule_versions or 0),
        "groups": groups,
    }


def get_axis_score_model_version_distribution(
    db: Session, days: int = 30
) -> Dict[str, Any]:
    """Compatibility name for callers using the service-specific description."""
    return get_model_version_distribution(db, days=days)


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
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/axis-scores/model-versions")
    def model_versions(days: int = 30, db: Session = Depends(get_session)):
        result = get_model_version_distribution(db, days=days)
        return {"days": days, **result}

    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="security",
                    model_version="model-a",
                    decision_rule_version="rule-a",
                    p_top=0.8,
                    escalated=True,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="privacy",
                    model_version="model-a",
                    decision_rule_version="rule-a",
                    p_top=0.6,
                    escalated=False,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-2",
                    axis_name="security",
                    model_version="model-b",
                    decision_rule_version="rule-b",
                    p_top=0.4,
                    escalated=True,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-3",
                    axis_name="availability",
                    model_version="model-b",
                    decision_rule_version="rule-b",
                    p_top=0.2,
                    escalated=False,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    response = TestClient(local_app).get(
        "/api/axis-scores/model-versions", params={"days": 30}
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["servers_scored"] == 3
    assert len(payload["groups"]) == 2
    assert max(group["server_count"] for group in payload["groups"]) == 2
    print("PASS")