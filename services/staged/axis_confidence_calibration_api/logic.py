"""logic.py -- the service's data/computation layer (one concern, one file).

Provides read-only confidence-calibration statistics from the real application
Postgres data layer. The router is kept thin and all database access is
performed through the application's SQLAlchemy session and ORM models.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


KNOWN_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisConfidence(BaseModel):
    axis_name: str
    count: int
    mean_p_top: float
    max_p_top: float
    escalated_count: int
    escalation_rate: float
    latest_model_version: Optional[str]
    latest_decision_rule_version: Optional[str]


class AxisConfidenceCalibrationResponse(BaseModel):
    axes: List[AxisConfidence]
    days: Optional[int]
    generated_at: str


router = APIRouter(prefix="/api")


def get_axis_confidence_calibration(
    db: Session,
    axis_name: Optional[str] = None,
    days: Optional[int] = None,
) -> Dict[str, object]:
    """Return aggregate confidence and escalation statistics for score axes."""
    if axis_name is not None and axis_name not in KNOWN_AXES:
        raise ValueError(f"Unknown axis_name: {axis_name}")
    if days is not None and days < 0:
        raise ValueError("days must be non-negative")

    query = select(
        McpLlmAxisScore.axis_name,
        func.count(McpLlmAxisScore.id).label("count"),
        func.coalesce(func.avg(McpLlmAxisScore.p_top), 0.0).label("mean_p_top"),
        func.coalesce(func.max(McpLlmAxisScore.p_top), 0.0).label("max_p_top"),
        func.sum(
            case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
        ).label("escalated_count"),
    ).where(McpLlmAxisScore.axis_name.in_(KNOWN_AXES))

    if axis_name is not None:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(McpLlmAxisScore.scored_at >= cutoff)

    query = query.group_by(McpLlmAxisScore.axis_name)
    aggregate_rows = db.execute(query).all()

    # Fetch the matching rows separately to identify the most recent model and
    # decision-rule versions without relying on PostgreSQL-specific DISTINCT ON.
    versions_query = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.id,
    ).where(McpLlmAxisScore.axis_name.in_(KNOWN_AXES))

    if axis_name is not None:
        versions_query = versions_query.where(McpLlmAxisScore.axis_name == axis_name)

    if days is not None:
        versions_query = versions_query.where(
            McpLlmAxisScore.scored_at >= cutoff
        )

    versions_query = versions_query.order_by(
        McpLlmAxisScore.axis_name.asc(),
        McpLlmAxisScore.scored_at.desc().nullslast(),
        McpLlmAxisScore.id.desc(),
    )
    version_rows = db.execute(versions_query).all()

    latest_versions: Dict[str, tuple[Optional[str], Optional[str]]] = {}
    for row in version_rows:
        if row.axis_name not in latest_versions:
            latest_versions[row.axis_name] = (
                row.model_version,
                row.decision_rule_version,
            )

    axes: List[AxisConfidence] = []
    for row in aggregate_rows:
        count = int(row.count or 0)
        escalated_count = int(row.escalated_count or 0)
        model_version, decision_rule_version = latest_versions.get(
            row.axis_name, (None, None)
        )
        axes.append(
            AxisConfidence(
                axis_name=row.axis_name,
                count=count,
                mean_p_top=float(row.mean_p_top or 0.0),
                max_p_top=float(row.max_p_top or 0.0),
                escalated_count=escalated_count,
                escalation_rate=(escalated_count / count) if count else 0.0,
                latest_model_version=model_version,
                latest_decision_rule_version=decision_rule_version,
            )
        )

    return {
        "axes": axes,
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/axes/confidence-calibration",
    response_model=AxisConfidenceCalibrationResponse,
)
def axis_confidence_calibration(
    axis_name: Optional[str] = None,
    days: Optional[int] = None,
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Read-only endpoint for per-axis confidence and escalation statistics."""
    try:
        return get_axis_confidence_calibration(
            db=db,
            axis_name=axis_name,
            days=days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    McpLlmAxisScore.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    now = datetime.now(timezone.utc)
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id=1,
                axis_name="overall_risk",
                model_version="model-a",
                decision_rule_version="rules-1",
                p_top=0.8,
                p_critical=0.1,
                p_danger=0.2,
                escalated=True,
                escalated_to="security",
                label="high",
                label_index=0,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id=2,
                axis_name="overall_risk",
                model_version="model-a",
                decision_rule_version="rules-1",
                p_top=0.6,
                p_critical=0.05,
                p_danger=0.1,
                escalated=False,
                label="medium",
                label_index=1,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=3,
                server_id=3,
                axis_name="auth_strength",
                model_version="model-b",
                decision_rule_version="rules-2",
                p_top=0.4,
                p_critical=0.02,
                p_danger=0.03,
                escalated=False,
                label="low",
                label_index=0,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=4,
                server_id=4,
                axis_name="auth_strength",
                model_version="model-b",
                decision_rule_version="rules-2",
                p_top=0.2,
                p_critical=0.01,
                p_danger=0.02,
                escalated=False,
                label="low",
                label_index=1,
                scored_at=now,
            ),
        ]
    )
    session.commit()

    def override_get_session():
        yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/confidence-calibration")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 2
        overall = next(
            axis for axis in payload["axes"] if axis["axis_name"] == "overall_risk"
        )
        assert overall["escalated_count"] == 1
        assert 0.0 <= overall["mean_p_top"] <= 1.0

    session.query(McpLlmAxisScore).delete()
    session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/axes/confidence-calibration")
        assert response.status_code == 200
        assert response.json()["axes"] == []

    print("PASS")