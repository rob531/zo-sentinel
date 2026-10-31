"""logic.py -- model version distribution computation over the real app data layer."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_model_version_distribution(db: Session) -> Dict[str, Any]:
    """Return score-row and server counts grouped by model and decision rule."""
    grouped_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            func.count(McpLlmAxisScore.id),
            func.count(func.distinct(McpLlmAxisScore.server_id)),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .order_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
    ).all()

    latest_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            func.max(McpLlmAxisScore.scored_at),
        ).group_by(McpLlmAxisScore.model_version)
    ).all()
    latest_by_model = {
        model_version: scored_at
        for model_version, scored_at in latest_rows
    }

    model_versions = []
    total_rows = 0
    for model_version, decision_rule_version, rows, servers in grouped_rows:
        rows = int(rows or 0)
        servers = int(servers or 0)
        total_rows += rows
        model_versions.append(
            {
                "model_version": model_version,
                "decision_rule_version": decision_rule_version,
                "rows": rows,
                "servers": servers,
                "last_scored_at": latest_by_model.get(model_version),
            }
        )

    return {
        "model_versions": model_versions,
        "total_rows": total_rows,
        "total_models": len(latest_by_model),
    }


# Compatibility aliases for staged consumers using the service's shorter names.
model_version_distribution = get_model_version_distribution
get_model_versions = get_model_version_distribution


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed_session = SessionLocal()
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    seed_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                axis_name="safety",
                decision_rule_version="rule-a",
                model_version="model-a",
                server_id="server-1",
                scored_at=base_time,
            ),
            McpLlmAxisScore(
                id=2,
                axis_name="safety",
                decision_rule_version="rule-a",
                model_version="model-a",
                server_id="server-2",
                scored_at=base_time + timedelta(minutes=1),
            ),
            McpLlmAxisScore(
                id=3,
                axis_name="safety",
                decision_rule_version="rule-b",
                model_version="model-b",
                server_id="server-3",
                scored_at=base_time,
            ),
        ]
    )
    seed_session.commit()
    seed_session.close()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/scoring/model-versions")
    def model_versions_route(db: Session = Depends(get_session)):
        return get_model_version_distribution(db)

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/scoring/model-versions")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["model_versions"]) == 2
    assert max(group["rows"] for group in payload["model_versions"]) == 2
    print("PASS")