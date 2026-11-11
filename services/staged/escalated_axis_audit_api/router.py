from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import aggregate, fetch_escalations


class AxisEscalationAggregate(BaseModel):
    axis_name: str
    count: int
    escalated_to: dict[str, int]


class EscalationAuditResponse(BaseModel):
    days: int
    total_escalations: int
    by_axis: list[AxisEscalationAggregate]
    top_target: Optional[str]
    rule_versions: list[str]


router = APIRouter(prefix="/api")


@router.get(
    "/axes/escalations",
    response_model=EscalationAuditResponse,
    summary="Audit axis-score escalations",
)
def audit_axis_escalations(
    days: int = Query(default=30, ge=1),
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = fetch_escalations(session, since)
    result = aggregate(rows)
    return {
        "days": days,
        "total_escalations": result["total_escalations"],
        "by_axis": result["by_axis"],
        "top_target": result.get("top_target"),
        "rule_versions": result.get("rule_versions", []),
    }


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": 1,
            "server_id": "server-auth-1",
            "axis_name": "auth_strength",
            "label": "weak",
            "label_index": 0,
            "p_top": 0.1,
            "p_critical": 0.2,
            "p_danger": 0.7,
            "escalated": True,
            "escalated_to": "ENTERPRISE_CONTROLLED",
            "decision_rule_version": "rules-v1",
            "model_version": "model-v1",
            "scored_at": now,
        },
        {
            "id": 2,
            "server_id": "server-auth-2",
            "axis_name": "auth_strength",
            "label": "weak",
            "label_index": 0,
            "p_top": 0.1,
            "p_critical": 0.2,
            "p_danger": 0.7,
            "escalated": True,
            "escalated_to": "ENTERPRISE_CONTROLLED",
            "decision_rule_version": "rules-v1",
            "model_version": "model-v1",
            "scored_at": now,
        },
        {
            "id": 3,
            "server_id": "server-network",
            "axis_name": "network_egress",
            "label": "exposed",
            "label_index": 0,
            "p_top": 0.1,
            "p_critical": 0.2,
            "p_danger": 0.7,
            "escalated": True,
            "escalated_to": "HIGH_RISK_ISOLATED",
            "decision_rule_version": "rules-v1",
            "model_version": "model-v1",
            "scored_at": now,
        },
        {
            "id": 4,
            "server_id": "server-overall",
            "axis_name": "overall_risk",
            "label": "normal",
            "label_index": 0,
            "p_top": 0.8,
            "p_critical": 0.1,
            "p_danger": 0.1,
            "escalated": False,
            "escalated_to": None,
            "decision_rule_version": "rules-v1",
            "model_version": "model-v1",
            "scored_at": now,
        },
    ]

    db = Session()
    try:
        db.execute(insert(McpLlmAxisScore), rows)
        db.commit()
    finally:
        db.close()

    with TestClient(local_app) as client:
        response = client.get("/api/axes/escalations", params={"days": 30})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_escalations"] == 3
    assert any(
        item["axis_name"] == "auth_strength" and item["count"] == 2
        for item in payload["by_axis"]
    )
    assert payload["top_target"] is not None
    print("PASS")


if __name__ == "__main__":
    _self_test()