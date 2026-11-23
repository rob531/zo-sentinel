"""Rule-version rollout data and computation over the authoritative app models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class RuleVersionRolloutEntry(BaseModel):
    decision_rule_version: str
    model_version: str
    distinct_servers: int
    score_rows: int
    axes: int
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None
    server_share_pct: float
    verdict_mix: Dict[str, int]


class RuleVersionRolloutResponse(BaseModel):
    total_servers: int
    versions: List[RuleVersionRolloutEntry]
    stale_version_servers: int


def _version_value(value: Any) -> str:
    return str(value) if value is not None else "unknown"


def _latest_score_rows(db: Session) -> List[Any]:
    row_number = func.row_number().over(
        partition_by=McpLlmAxisScore.server_id,
        order_by=(
            McpLlmAxisScore.scored_at.desc().nulls_last(),
            McpLlmAxisScore.id.desc(),
        ),
    ).label("_row_number")

    latest = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.scored_at,
            row_number,
        )
        .subquery()
    )

    return list(
        db.execute(
            select(
                latest.c.server_id,
                latest.c.decision_rule_version,
                latest.c.model_version,
                latest.c.scored_at,
            ).where(latest.c._row_number == 1)
        ).all()
    )


def _verdict_mix_by_version(db: Session) -> Dict[tuple[str, str], Dict[str, int]]:
    row_number = func.row_number().over(
        partition_by=(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.server_id,
        ),
        order_by=(
            McpLlmAxisScore.scored_at.desc().nulls_last(),
            McpLlmAxisScore.id.desc(),
        ),
    ).label("_row_number")

    latest = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            row_number,
        )
        .subquery()
    )

    rows = db.execute(
        select(
            latest.c.decision_rule_version,
            latest.c.model_version,
            McpServerRegistry.verdict,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == latest.c.server_id,
        )
        .where(latest.c._row_number == 1)
    ).all()

    mixes: Dict[tuple[str, str], Dict[str, int]] = {}
    for decision_rule_version, model_version, verdict in rows:
        key = (
            _version_value(decision_rule_version),
            _version_value(model_version),
        )
        mix = mixes.setdefault(key, {})
        verdict_name = str(verdict) if verdict is not None else "UNKNOWN"
        mix[verdict_name] = mix.get(verdict_name, 0) + 1

    return mixes


def get_rule_version_rollout(db: Session) -> Dict[str, Any]:
    """Summarize scoring activity and verdict mix by rule/model version."""
    aggregate_rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            func.count(McpLlmAxisScore.server_id.distinct()).label(
                "distinct_servers"
            ),
            func.count().label("score_rows"),
            func.count(McpLlmAxisScore.axis_name.distinct()).label("axes"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        ).group_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
        )
    ).all()

    total_servers = int(
        db.execute(
            select(func.count(McpLlmAxisScore.server_id.distinct()))
        ).scalar()
        or 0
    )

    verdict_mixes = _verdict_mix_by_version(db)
    versions: List[Dict[str, Any]] = []

    for (
        decision_rule_version,
        model_version,
        distinct_servers,
        score_rows,
        axes,
        first_scored_at,
        last_scored_at,
    ) in aggregate_rows:
        decision_name = _version_value(decision_rule_version)
        model_name = _version_value(model_version)
        server_count = int(distinct_servers or 0)
        versions.append(
            {
                "decision_rule_version": decision_name,
                "model_version": model_name,
                "distinct_servers": server_count,
                "score_rows": int(score_rows or 0),
                "axes": int(axes or 0),
                "first_scored_at": first_scored_at,
                "last_scored_at": last_scored_at,
                "server_share_pct": round(
                    (server_count / total_servers * 100.0) if total_servers else 0.0,
                    2,
                ),
                "verdict_mix": verdict_mixes.get(
                    (decision_name, model_name), {}
                ),
            }
        )

    versions.sort(
        key=lambda item: (
            item["last_scored_at"] is not None,
            item["last_scored_at"].isoformat()
            if item["last_scored_at"] is not None
            else "",
            item["distinct_servers"],
            item["decision_rule_version"],
            item["model_version"],
        ),
        reverse=True,
    )

    stale_version_servers = 0
    if versions:
        current_version = (
            versions[0]["decision_rule_version"],
            versions[0]["model_version"],
        )
        for row in _latest_score_rows(db):
            server_version = (
                _version_value(row.decision_rule_version),
                _version_value(row.model_version),
            )
            if server_version != current_version:
                stale_version_servers += 1

    return {
        "total_servers": total_servers,
        "versions": versions,
        "stale_version_servers": stale_version_servers,
    }


router = APIRouter()


@router.get(
    "/scoring/rule-version-rollout",
    response_model=RuleVersionRolloutResponse,
)
def rule_version_rollout(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_rule_version_rollout(db)


if __name__ == "__main__":
    from datetime import timezone

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
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    seeded = SessionLocal()
    try:
        seeded.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    verdict="TRUSTED_GENERAL",
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    verdict="SUSPICIOUS",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    verdict="MALICIOUS",
                    risk_tier="HIGH",
                ),
            ]
        )
        seeded.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="code_quality",
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    scored_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-1",
                    axis_name="security",
                    decision_rule_version="rule-v1",
                    model_version="model-v1",
                    scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-1",
                    axis_name="code_quality",
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    scored_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-2",
                    axis_name="code_quality",
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    scored_at=datetime(2025, 2, 2, tzinfo=timezone.utc),
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="server-3",
                    axis_name="code_quality",
                    decision_rule_version="rule-v2",
                    model_version="model-v2",
                    scored_at=datetime(2025, 2, 3, tzinfo=timezone.utc),
                ),
            ]
        )
        seeded.commit()
    finally:
        seeded.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/rule-version-rollout")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["versions"]) == 2, payload
        newer = payload["versions"][0]
        older = payload["versions"][1]
        assert newer["distinct_servers"] > older["distinct_servers"], payload
        assert all(entry.get("last_scored_at") for entry in payload["versions"]), payload

    engine.dispose()
    print("PASS")