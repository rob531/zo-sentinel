from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
_VALID_AXIS_SET = set(VALID_AXES)


class AxisEscalation(BaseModel):
    axis_name: str
    count: int
    escalated_to: Dict[str, int] = Field(default_factory=dict)


class AxisEscalationResponse(BaseModel):
    days: int
    total_escalations: int
    by_axis: List[AxisEscalation]
    top_target: Optional[str] = None
    rule_versions: List[str] = Field(default_factory=list)


def fetch_escalations(session, since: datetime) -> List[dict]:
    rows = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= since,
        )
        .all()
    )

    return [
        {
            "axis_name": row.axis_name,
            "escalated_to": row.escalated_to,
            "decision_rule_version": row.decision_rule_version,
        }
        for row in rows
    ]


def aggregate(rows: List[dict]) -> dict:
    axis_counts: Dict[str, int] = {}
    target_counts: Dict[str, int] = {}
    target_by_axis: Dict[str, Dict[str, int]] = {}
    rule_versions = set()

    for row in rows:
        raw_axis = row.get("axis_name")
        axis_name = raw_axis if raw_axis in _VALID_AXIS_SET else "unknown"

        target = row.get("escalated_to")
        target = str(target) if target is not None else "unknown"

        axis_counts[axis_name] = axis_counts.get(axis_name, 0) + 1
        target_counts[target] = target_counts.get(target, 0) + 1
        target_by_axis.setdefault(axis_name, {})
        target_by_axis[axis_name][target] = target_by_axis[axis_name].get(target, 0) + 1

        version = row.get("decision_rule_version")
        if version is not None:
            rule_versions.add(str(version))

    ordered_axes = [axis for axis in VALID_AXES if axis in axis_counts]
    if "unknown" in axis_counts:
        ordered_axes.append("unknown")

    by_axis = [
        {
            "axis_name": axis,
            "count": axis_counts[axis],
            "escalated_to": target_by_axis.get(axis, {}),
        }
        for axis in ordered_axes
    ]

    top_target = None
    if target_counts:
        top_target = sorted(target_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    return {
        "total_escalations": len(rows),
        "by_axis": by_axis,
        "top_target": top_target,
        "rule_versions": sorted(rule_versions),
    }


router = APIRouter(prefix="/api")


@router.get("/axes/escalations", response_model=AxisEscalationResponse)
def get_axis_escalations(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    since = datetime.utcnow() - timedelta(days=days)
    rows = fetch_escalations(session, since)
    result = aggregate(rows)
    result["days"] = days
    return result


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    now = datetime.utcnow()
    test_session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-auth-1",
                axis_name="auth_strength",
                label="auth",
                label_index=1,
                p_top=0.91,
                p_critical=0.04,
                p_danger=0.05,
                escalated=True,
                escalated_to="ENTERPRISE_CONTROLLED",
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-auth-2",
                axis_name="auth_strength",
                label="auth",
                label_index=1,
                p_top=0.89,
                p_critical=0.03,
                p_danger=0.08,
                escalated=True,
                escalated_to="ENTERPRISE_CONTROLLED",
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-network",
                axis_name="network_egress",
                label="egress",
                label_index=2,
                p_top=0.82,
                p_critical=0.02,
                p_danger=0.16,
                escalated=True,
                escalated_to="HIGH_RISK_ISOLATED",
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=4,
                server_id="server-normal",
                axis_name="overall_risk",
                label="overall",
                label_index=0,
                p_top=0.12,
                p_critical=0.01,
                p_danger=0.02,
                escalated=False,
                escalated_to=None,
                decision_rule_version="rules-v1",
                model_version="model-v1",
                scored_at=now,
            ),
        ]
    )
    test_session.commit()

    def override_session():
        yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_session

    with TestClient(that_app) as client:
        response = client.get("/api/axes/escalations", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_escalations"] == 3, payload
        auth_axes = [
            item for item in payload["by_axis"] if item["axis_name"] == "auth_strength"
        ]
        assert auth_axes and auth_axes[0]["count"] == 2, payload
        assert payload["top_target"] is not None, payload

    test_session.close()
    test_engine.dispose()
    print("PASS")