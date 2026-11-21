"""Data and computation layer for decision-rule-version score summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXIS_NAMES = (
    "malicious_intent",
    "data_exfiltration",
    "code_execution",
    "credential_access",
    "privilege_escalation",
    "persistence",
    "evasion",
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _float(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def build_decision_rule_version_report(db: Session) -> Dict[str, Any]:
    """Build decision-rule, model, adapter, and mixed-server summaries."""
    valid_axes = McpLlmAxisScore.axis_name.in_(VALID_AXIS_NAMES)

    decision_rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "server_count"
            ),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label(
                "distinct_axes"
            ),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.sum(
                case(
                    (McpLlmAxisScore.escalated.is_(True), 1),
                    else_=0,
                )
            ).label("escalated_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(valid_axes)
        .group_by(McpLlmAxisScore.decision_rule_version)
        .order_by(McpLlmAxisScore.decision_rule_version)
    ).all()

    decision_rule_versions: List[Dict[str, Any]] = [
        {
            "decision_rule_version": version,
            "row_count": int(row_count),
            "server_count": int(server_count),
            "distinct_axes": int(distinct_axes),
            "first_scored_at": _iso(first_scored_at),
            "last_scored_at": _iso(last_scored_at),
            "escalated_count": int(escalated_count or 0),
            "avg_p_top": _float(avg_p_top),
        }
        for (
            version,
            row_count,
            server_count,
            distinct_axes,
            first_scored_at,
            last_scored_at,
            escalated_count,
            avg_p_top,
        ) in decision_rows
    ]

    model_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "server_count"
            ),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(valid_axes)
        .group_by(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.model_version)
    ).all()

    model_versions: List[Dict[str, Any]] = [
        {
            "model_version": model_version,
            "row_count": int(row_count),
            "server_count": int(server_count),
            "first_scored_at": _iso(first_scored_at),
            "last_scored_at": _iso(last_scored_at),
        }
        for (
            model_version,
            row_count,
            server_count,
            first_scored_at,
            last_scored_at,
        ) in model_rows
    ]

    adapter_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label(
                "server_count"
            ),
        )
        .where(valid_axes)
        .group_by(McpLlmAxisScore.adapter_sha256)
        .order_by(McpLlmAxisScore.adapter_sha256)
    ).all()

    adapter_sha256_variants: List[Dict[str, Any]] = [
        {
            "adapter_sha256": adapter_sha256,
            "row_count": int(row_count),
            "server_count": int(server_count),
        }
        for adapter_sha256, row_count, server_count in adapter_rows
    ]

    mixed_servers = (
        select(McpLlmAxisScore.server_id)
        .where(valid_axes)
        .group_by(McpLlmAxisScore.server_id)
        .having(
            func.count(
                func.distinct(McpLlmAxisScore.decision_rule_version)
            )
            > 1
        )
    )
    mixed_version_servers = list(
        db.execute(
            select(McpLlmAxisScore.server_id).where(
                McpLlmAxisScore.server_id.in_(mixed_servers)
            )
        )
        .scalars()
        .all()
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "decision_rule_versions": decision_rule_versions,
        "model_versions": model_versions,
        "adapter_sha256_variants": adapter_sha256_variants,
        "mixed_version_servers": mixed_version_servers,
    }


def get_decision_rule_version_report(db: Session) -> Dict[str, Any]:
    return build_decision_rule_version_report(db)


def get_decision_rule_version_summary(db: Session) -> Dict[str, Any]:
    return build_decision_rule_version_report(db)


def get_decision_rule_version_metrics(db: Session) -> Dict[str, Any]:
    return build_decision_rule_version_report(db)


if __name__ == "__main__":
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
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="mixed-server",
                    axis_name=VALID_AXIS_NAMES[0],
                    p_top=0.91,
                    p_critical=0.51,
                    p_danger=0.41,
                    escalated=True,
                    escalated_to="critical",
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-a",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="mixed-server",
                    axis_name=VALID_AXIS_NAMES[1],
                    p_top=0.83,
                    p_critical=0.22,
                    p_danger=0.35,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    adapter_sha256="adapter-b",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="stable-server-a",
                    axis_name=VALID_AXIS_NAMES[2],
                    p_top=0.76,
                    p_critical=0.12,
                    p_danger=0.28,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    adapter_sha256="adapter-a",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="stable-server-b",
                    axis_name=VALID_AXIS_NAMES[3],
                    p_top=0.88,
                    p_critical=0.32,
                    p_danger=0.47,
                    escalated=True,
                    escalated_to="danger",
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    adapter_sha256="adapter-b",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/scores/decision-rule-versions")
    def decision_rule_versions_endpoint(session: Session = Depends(get_session)):
        return build_decision_rule_version_report(session)

    with TestClient(test_app) as client:
        response = client.get("/api/scores/decision-rule-versions")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["decision_rule_versions"]) == 2, payload
        assert "mixed-server" in payload["mixed_version_servers"], payload
        assert all(
            entry["row_count"] > 0
            for entry in payload["decision_rule_versions"]
        ), payload
        assert all(
            entry["row_count"] > 0 for entry in payload["model_versions"]
        ), payload
        assert all(
            entry["row_count"] > 0
            for entry in payload["adapter_sha256_variants"]
        ), payload

    test_engine.dispose()
    print("PASS")