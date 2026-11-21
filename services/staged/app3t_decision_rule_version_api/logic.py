"""Data and aggregation logic for the decision-rule-version scores API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXIS_NAMES = (
    "authentication",
    "authorization",
    "confidentiality",
    "integrity",
    "availability",
    "privacy",
    "safety",
)


def _valid_axis_filter():
    return McpLlmAxisScore.axis_name.in_(VALID_AXIS_NAMES)


def get_decision_rule_versions(db: Session) -> Dict[str, Any]:
    """Aggregate valid LLM axis scores by rule version, model, and adapter."""
    version_rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.count(func.distinct(McpLlmAxisScore.axis_name)).label("distinct_axes"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.coalesce(
                func.sum(
                    case(
                        (McpLlmAxisScore.escalated.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("escalated_count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(_valid_axis_filter())
        .group_by(McpLlmAxisScore.decision_rule_version)
        .order_by(McpLlmAxisScore.decision_rule_version)
    ).all()

    decision_rule_versions: List[Dict[str, Any]] = [
        {
            "decision_rule_version": row.decision_rule_version,
            "row_count": int(row.row_count),
            "server_count": int(row.server_count),
            "distinct_axes": int(row.distinct_axes),
            "first_scored_at": row.first_scored_at,
            "last_scored_at": row.last_scored_at,
            "escalated_count": int(row.escalated_count or 0),
            "avg_p_top": float(row.avg_p_top) if row.avg_p_top is not None else None,
        }
        for row in version_rows
    ]

    model_rows = db.execute(
        select(
            McpLlmAxisScore.model_version,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(_valid_axis_filter())
        .group_by(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.model_version)
    ).all()

    model_versions: List[Dict[str, Any]] = [
        {
            "model_version": row.model_version,
            "row_count": int(row.row_count),
            "server_count": int(row.server_count),
            "first_scored_at": row.first_scored_at,
            "last_scored_at": row.last_scored_at,
        }
        for row in model_rows
    ]

    adapter_rows = db.execute(
        select(
            McpLlmAxisScore.adapter_sha256,
            func.count().label("row_count"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
        )
        .where(_valid_axis_filter())
        .group_by(McpLlmAxisScore.adapter_sha256)
        .order_by(McpLlmAxisScore.adapter_sha256)
    ).all()

    adapter_sha256_variants: List[Dict[str, Any]] = [
        {
            "adapter_sha256": row.adapter_sha256,
            "row_count": int(row.row_count),
            "server_count": int(row.server_count),
        }
        for row in adapter_rows
    ]

    versioned_score = aliased(McpLlmAxisScore)
    mixed_versions = (
        select(versioned_score.server_id)
        .where(_valid_axis_filter())
        .group_by(versioned_score.server_id)
        .having(
            func.count(func.distinct(versioned_score.decision_rule_version)) > 1
        )
        .subquery()
    )
    mixed_version_servers = [
        server_id
        for server_id in db.execute(
            select(mixed_versions.c.server_id).order_by(mixed_versions.c.server_id)
        ).scalars()
        if server_id is not None
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "decision_rule_versions": decision_rule_versions,
        "model_versions": model_versions,
        "adapter_sha256_variants": adapter_sha256_variants,
        "mixed_version_servers": mixed_version_servers,
    }


def get_decision_rule_version_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers that name the aggregate a summary."""
    return get_decision_rule_versions(db)


def decision_rule_version_summary(db: Session) -> Dict[str, Any]:
    """Compatibility alias for callers that name the aggregate a summary."""
    return get_decision_rule_versions(db)


if __name__ == "__main__":
    from datetime import timedelta

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

    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-mixed",
                    axis_name=VALID_AXIS_NAMES[0],
                    p_top=0.90,
                    p_critical=0.05,
                    p_danger=0.05,
                    escalated=True,
                    escalated_to="critical",
                    decision_rule_version="rule-v1",
                    model_version="model-1",
                    adapter_sha256="adapter-a",
                    scored_at=base_time,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-mixed",
                    axis_name=VALID_AXIS_NAMES[1],
                    p_top=0.80,
                    p_critical=0.10,
                    p_danger=0.10,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v2",
                    model_version="model-2",
                    adapter_sha256="adapter-b",
                    scored_at=base_time + timedelta(minutes=1),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-one",
                    axis_name=VALID_AXIS_NAMES[2],
                    p_top=0.70,
                    p_critical=0.15,
                    p_danger=0.15,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rule-v1",
                    model_version="model-1",
                    adapter_sha256="adapter-a",
                    scored_at=base_time + timedelta(minutes=2),
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-two",
                    axis_name=VALID_AXIS_NAMES[3],
                    p_top=0.60,
                    p_critical=0.20,
                    p_danger=0.20,
                    escalated=True,
                    escalated_to="danger",
                    decision_rule_version="rule-v2",
                    model_version="model-2",
                    adapter_sha256="adapter-b",
                    scored_at=base_time + timedelta(minutes=3),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/scores/decision-rule-versions")
    def decision_rule_versions_endpoint(db: Session = Depends(get_session)):
        return get_decision_rule_versions(db)

    def override_get_session():
        with SessionLocal() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/scores/decision-rule-versions")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["decision_rule_versions"]) == 2
    assert "server-mixed" in payload["mixed_version_servers"]
    assert all(entry["row_count"] > 0 for entry in payload["decision_rule_versions"])
    assert all(entry["row_count"] > 0 for entry in payload["model_versions"])
    assert all(entry["row_count"] > 0 for entry in payload["adapter_sha256_variants"])
    print("PASS")