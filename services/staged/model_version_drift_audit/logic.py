"""Logic layer for model version drift auditing."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class ModelVersionDriftGroup(BaseModel):
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None
    decision_rule_version: Optional[str] = None
    rows: int
    servers: int
    first_scored_at: str
    last_scored_at: str
    overall_risk_rows: int


class ModelVersionDriftResponse(BaseModel):
    groups: List[ModelVersionDriftGroup]
    mixed_provenance_servers: int
    generated_at: str


ModelVersionDriftGroupResponse = ModelVersionDriftGroup
ModelVersionDriftEnvelope = ModelVersionDriftResponse


def _timestamp(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def compute_model_version_drift_audit(db: Session) -> Dict[str, object]:
    """Compute drift groups and servers with mixed model provenance."""
    score = McpLlmAxisScore
    overall_risk = case(
        (score.axis_name == "overall_risk", 1),
        else_=0,
    )

    rows = db.execute(
        select(
            score.model_version,
            score.adapter_sha256,
            score.decision_rule_version,
            func.count(score.id),
            func.count(func.distinct(score.server_id)),
            func.min(score.scored_at),
            func.max(score.scored_at),
            func.sum(overall_risk),
        )
        .group_by(
            score.model_version,
            score.adapter_sha256,
            score.decision_rule_version,
        )
    ).all()

    groups: List[Dict[str, object]] = []
    for (
        model_version,
        adapter_sha256,
        decision_rule_version,
        row_count,
        server_count,
        first_scored_at,
        last_scored_at,
        overall_risk_rows,
    ) in rows:
        row_count = int(row_count or 0)
        overall_risk_rows = int(overall_risk_rows or 0)
        _overall_risk_share = (
            float(overall_risk_rows) / float(row_count) if row_count else 0.0
        )
        groups.append(
            {
                "model_version": model_version,
                "adapter_sha256": adapter_sha256,
                "decision_rule_version": decision_rule_version,
                "rows": row_count,
                "servers": int(server_count or 0),
                "first_scored_at": _timestamp(first_scored_at),
                "last_scored_at": _timestamp(last_scored_at),
                "overall_risk_rows": overall_risk_rows,
            }
        )

    mixed_servers = (
        select(score.server_id)
        .where(
            score.server_id.is_not(None),
            score.model_version.is_not(None),
        )
        .group_by(score.server_id)
        .having(func.count(func.distinct(score.model_version)) > 1)
    )
    mixed_provenance_servers = int(
        db.execute(
            select(func.count(func.distinct(score.server_id))).where(
                score.server_id.in_(mixed_servers)
            )
        ).scalar()
        or 0
    )

    return {
        "groups": groups,
        "mixed_provenance_servers": mixed_provenance_servers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_model_version_drift(db: Session) -> Dict[str, object]:
    return compute_model_version_drift_audit(db)


def get_model_version_drift(db: Session) -> Dict[str, object]:
    return compute_model_version_drift_audit(db)


def build_model_version_drift(db: Session) -> Dict[str, object]:
    return compute_model_version_drift_audit(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    local_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine)

    def override_session():
        with session_factory() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_session
    McpLlmAxisScore.__table__.create(engine)

    with session_factory() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    model_version="model-a",
                    adapter_sha256="adapter-a",
                    decision_rule_version="rules-1",
                    axis_name="overall_risk",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-1",
                    model_version="model-b",
                    adapter_sha256="adapter-b",
                    axis_name="risk",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-2",
                    model_version="model-b",
                    adapter_sha256="adapter-b",
                    axis_name="overall_risk",
                ),
            ]
        )
        session.commit()

        result = compute_model_version_drift_audit(session)
        assert len(result["groups"]) == 2
        assert result["mixed_provenance_servers"] == 1
        assert sum(group["overall_risk_rows"] for group in result["groups"]) == 2

    print("PASS")