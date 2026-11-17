"""Data and computation layer for the axis model version audit API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


LEGAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _as_text(value: Any) -> str:
    """Convert database timestamp values to the API's string representation."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_axis_model_versions(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """Return scoring-model combinations currently represented by axis scores.

    A combination is identified by its model version, adapter checksum, and
    decision-rule version.  The registry is left-joined so that a score is
    still reported when its server has not yet acquired a registry row.
    """
    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
            func.count(func.distinct(McpLlmAxisScore.server_id)),
            func.max(McpLlmAxisScore.scored_at),
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name.in_(LEGAL_AXES))
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.decision_rule_version,
        )
    ).all()

    by_axis: Dict[str, List[Dict[str, Any]]] = {}
    for axis_name, model_version, adapter_sha256, decision_rule_version, servers, latest in rows:
        by_axis.setdefault(str(axis_name), []).append(
            {
                "model_version": str(model_version or ""),
                "adapter_sha256": str(adapter_sha256 or ""),
                "decision_rule_version": str(decision_rule_version or ""),
                "servers": int(servers or 0),
                "latest_scored_at": _as_text(latest),
                "mixed": False,
            }
        )

    axes: List[Dict[str, Any]] = []
    for axis_name in LEGAL_AXES:
        combinations = by_axis.get(axis_name)
        if not combinations:
            continue

        distinct_combinations = len(combinations)
        for combination in combinations:
            combination["mixed"] = distinct_combinations > 1

        axes.append(
            {
                "axis": axis_name,
                "combinations": combinations,
                "distinct_combinations": distinct_combinations,
            }
        )

    return {"axes": axes}


def get_axis_model_versions_endpoint(
    db: Session = Depends(get_session),
) -> Dict[str, List[Dict[str, Any]]]:
    """FastAPI dependency-injection wrapper for the audit computation."""
    return get_axis_model_versions(db)


# Keep descriptive aliases available to routers and service-level callers.
get_axis_model_version_audit = get_axis_model_versions
axis_model_version_audit = get_axis_model_versions


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
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)

    testing_session = sessionmaker(bind=engine)()
    try:
        testing_session.add_all(
            [
                McpServerRegistry(server_id="server-a", risk_tier="high"),
                McpServerRegistry(server_id="server-b", risk_tier="medium"),
                McpServerRegistry(server_id="server-c", risk_tier="low"),
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name="overall_risk",
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                    decision_rule_version="rules-v1",
                    scored_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-b",
                    axis_name="overall_risk",
                    model_version="model-v2",
                    adapter_sha256="adapter-v2",
                    decision_rule_version="rules-v1",
                    scored_at=datetime(2024, 1, 2, 12, 0, 0),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-c",
                    axis_name="auth_strength",
                    model_version="model-v1",
                    adapter_sha256="adapter-v1",
                    decision_rule_version="rules-v1",
                    scored_at=datetime(2024, 1, 3, 12, 0, 0),
                ),
            ]
        )
        testing_session.commit()

        that_app = FastAPI()

        def override_session():
            yield testing_session

        that_app.dependency_overrides[get_session] = override_session

        @that_app.get("/api/axis-model-versions")
        def axis_model_versions_endpoint(db: Session = Depends(get_session)):
            return get_axis_model_versions(db)

        response = TestClient(that_app).get("/api/axis-model-versions")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 2
        assert all(axis["axis"] in LEGAL_AXES for axis in payload["axes"])
        overall = next(axis for axis in payload["axes"] if axis["axis"] == "overall_risk")
        assert overall["distinct_combinations"] == 2
        assert overall["combinations"][0]["mixed"] is True
        print("PASS")
    finally:
        testing_session.close()
        engine.dispose()