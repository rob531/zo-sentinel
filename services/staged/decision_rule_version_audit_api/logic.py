"""logic.py -- decision-rule version audit data/computation layer.

Reads the authoritative application database through the real SQLAlchemy models.
The router remains responsible for the HTTP response envelope.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def decision_rule_version_audit(db: Session) -> List[Dict[str, Any]]:
    """Return audit aggregates for each decision-rule/model/adapter version."""
    escalated_rows = func.sum(
        case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
    )

    rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            escalated_rows.label("escalated_rows"),
        )
        .group_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
        )
        .order_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
        )
    ).all()

    result: List[Dict[str, Any]] = []
    for (
        decision_rule_version,
        model_version,
        adapter_sha256,
        row_count,
        server_count,
        first_scored_at,
        last_scored_at,
        escalated_count,
    ) in rows:
        count = int(row_count or 0)
        escalated = int(escalated_count or 0)
        result.append(
            {
                "decision_rule_version": decision_rule_version,
                "model_version": model_version,
                "adapter_sha256": adapter_sha256,
                "row_count": count,
                "server_count": int(server_count or 0),
                "first_scored_at": first_scored_at,
                "last_scored_at": last_scored_at,
                "escalated_rows": escalated,
                "escalated_share": (escalated / count) if count else 0.0,
            }
        )
    return result


# Compatibility aliases for routers using a more explicit operation name.
get_decision_rule_version_audit = decision_rule_version_audit
list_decision_rule_versions = decision_rule_version_audit


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    axis_name="risk",
                    decision_rule_version="v1",
                    model_version="model-a",
                    adapter_sha256="adapter-a",
                    server_id="server-1",
                    escalated=True,
                    scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=2,
                    axis_name="risk",
                    decision_rule_version="v1",
                    model_version="model-a",
                    adapter_sha256="adapter-a",
                    server_id="server-2",
                    escalated=False,
                    scored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=3,
                    axis_name="risk",
                    decision_rule_version="v2",
                    model_version="model-b",
                    adapter_sha256="adapter-b",
                    server_id="server-3",
                    escalated=False,
                    scored_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scoring/decision-rule-audit")
    def audit(db: Session = Depends(get_session)):
        return {"versions": decision_rule_version_audit(db)}

    def override_session():
        with TestSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/decision-rule-audit")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["versions"]) == 2
        seeded = next(
            item
            for item in payload["versions"]
            if item["decision_rule_version"] == "v1"
        )
        assert seeded["escalated_share"] == 1 / 2
        assert seeded["row_count"] == 2
        assert seeded["server_count"] == 2

    print("PASS")