"""logic.py -- model and decision-rule version distribution for axis scores."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _timestamp_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, datetime):
        return (0, value)
    return (1, str(value))


def get_model_version_mix(db: Session) -> dict[str, Any]:
    """Return the score distribution grouped by model and decision-rule version."""
    rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
        )
    ).all()

    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for model_version, decision_rule_version, adapter_sha256, axis_name, scored_at, server_id in rows:
        model = model_version if model_version is not None else "unknown"
        decision = decision_rule_version if decision_rule_version is not None else "unknown"
        key = (str(model), str(decision))

        group = grouped.setdefault(
            key,
            {
                "adapters": set(),
                "score_count": 0,
                "servers": set(),
                "axes": set(),
                "timestamps": [],
            },
        )
        group["score_count"] += 1
        if adapter_sha256 is not None:
            group["adapters"].add(str(adapter_sha256)[:8])
        if server_id is not None:
            group["servers"].add(server_id)
        if axis_name is not None:
            group["axes"].add(axis_name)
        if scored_at is not None:
            group["timestamps"].append(scored_at)

    groups = []
    for (model, decision), group in grouped.items():
        timestamps = group["timestamps"]
        first = min(timestamps, key=_timestamp_key) if timestamps else None
        last = max(timestamps, key=_timestamp_key) if timestamps else None
        groups.append(
            {
                "model_version": model,
                "decision_rule_version": decision,
                "adapters": sorted(group["adapters"]),
                "score_count": int(group["score_count"]),
                "server_count": len(group["servers"]),
                "axis_count": len(group["axes"]),
                "first_scored_at": _timestamp(first),
                "last_scored_at": _timestamp(last),
            }
        )

    groups.sort(key=lambda group: (-group["score_count"], group["model_version"], group["decision_rule_version"]))

    return {
        "total_rows": len(rows),
        "group_count": len(groups),
        "groups": groups,
    }


# Compatibility aliases for staged-service callers.
get_axis_score_model_version_mix = get_model_version_mix
get_model_version_distribution = get_model_version_mix
build_model_version_mix = get_model_version_mix


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
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    model_version="model-a",
                    decision_rule_version="rule-a",
                    adapter_sha256="abcdef1234567890",
                    axis_name="security",
                    server_id="server-1",
                ),
                McpLlmAxisScore(
                    id=2,
                    model_version="model-a",
                    decision_rule_version="rule-a",
                    adapter_sha256="abcdef1234567890",
                    axis_name="quality",
                    server_id="server-2",
                ),
                McpLlmAxisScore(
                    id=3,
                    model_version="model-b",
                    decision_rule_version=None,
                    adapter_sha256="fedcba9876543210",
                    axis_name="security",
                    server_id="server-3",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/axis/model-version-mix")
    def model_version_mix(db: Session = Depends(get_session)):
        return get_model_version_mix(db)

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/axis/model-version-mix")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_rows"] == 3
        assert payload["group_count"] == 2
        unknown = [g for g in payload["groups"] if g["model_version"] == "model-b"]
        assert unknown and unknown[0]["decision_rule_version"] == "unknown"
        known = [g for g in payload["groups"] if g["model_version"] == "model-a"]
        assert known and known[0]["score_count"] == 2

    print("PASS")