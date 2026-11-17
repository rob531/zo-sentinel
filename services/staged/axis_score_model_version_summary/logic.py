"""logic.py -- read-only model-version summary for axis scoring data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _timestamp(value: Any) -> str:
    """Convert database timestamps to a stable ISO-8601 string."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def summarize_model_versions(db: Session) -> Dict[str, Any]:
    """Summarize current axis-score model variants using the live app session."""
    rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.scored_at,
        )
    ).all()

    model_rows: Dict[str, int] = {}
    model_servers: Dict[str, Set[str]] = {}
    model_escalated: Dict[str, int] = {}
    model_first: Dict[str, Any] = {}
    model_last: Dict[str, Any] = {}
    model_axes: Dict[str, Set[str]] = {}
    variants: Dict[Tuple[str, str, str], None] = {}

    for row in rows:
        model_version = str(row.model_version)
        adapter_sha256 = row.adapter_sha256 or ""
        decision_rule_version = row.decision_rule_version or ""
        variants[(model_version, adapter_sha256, decision_rule_version)] = None

        model_rows[model_version] = model_rows.get(model_version, 0) + 1
        model_servers.setdefault(model_version, set()).add(str(row.server_id))
        if row.escalated is True:
            model_escalated[model_version] = model_escalated.get(model_version, 0) + 1

        axes = model_axes.setdefault(model_version, set())
        if row.axis_name is not None:
            axes.add(str(row.axis_name))

        scored_at = row.scored_at
        if scored_at is not None:
            if model_version not in model_first or scored_at < model_first[model_version]:
                model_first[model_version] = scored_at
            if model_version not in model_last or scored_at > model_last[model_version]:
                model_last[model_version] = scored_at

    models: List[Dict[str, Any]] = []
    for model_version, adapter_sha256, decision_rule_version in variants:
        models.append(
            {
                "model_version": model_version,
                "adapter_sha256": adapter_sha256,
                "decision_rule_version": decision_rule_version,
                "row_count": model_rows[model_version],
                "server_count": len(model_servers.get(model_version, set())),
                "escalated_count": model_escalated.get(model_version, 0),
                "first_scored_at": _timestamp(model_first.get(model_version)),
                "last_scored_at": _timestamp(model_last.get(model_version)),
                "axes": sorted(model_axes.get(model_version, set())),
            }
        )

    models.sort(
        key=lambda item: (
            -item["row_count"],
            item["model_version"],
            item["adapter_sha256"],
            item["decision_rule_version"],
        )
    )

    return {
        "models": models,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def get_axis_model_versions(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """FastAPI dependency-compatible entry point for the summary."""
    return summarize_model_versions(db)


# Compatibility names for callers that use a shorter function name.
get_model_versions = get_axis_model_versions
model_version_summary = get_axis_model_versions


if __name__ == "__main__":
    from fastapi import FastAPI
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
    testing_session = sessionmaker(bind=engine)

    with testing_session() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    axis_name="authentication",
                    model_version="model-v1",
                    server_id="server-1",
                    adapter_sha256="adapter-a",
                    decision_rule_version="rules-v1",
                    escalated=True,
                    scored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=2,
                    axis_name="authorization",
                    model_version="model-v1",
                    server_id="server-2",
                    adapter_sha256="adapter-a",
                    decision_rule_version="rules-v1",
                    escalated=False,
                    scored_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=3,
                    axis_name="authentication",
                    model_version="model-v2",
                    server_id="server-3",
                    adapter_sha256="adapter-b",
                    decision_rule_version="rules-v1",
                    escalated=False,
                    scored_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/axis/model-versions")
    def axis_model_versions_endpoint(db: Session = Depends(get_session)):
        return get_axis_model_versions(db)

    response = TestClient(that_app).get("/api/axis/model-versions")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["models"]) == 2
    assert payload["models"][0]["row_count"] == 2
    assert payload["models"][0]["escalated_count"] == 1
    print("PASS")