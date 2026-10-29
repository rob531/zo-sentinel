from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_axis_escalation_target_mix
except ImportError:  # Allows direct execution of this module for its self-test.
    from services.staged.axis_escalation_target_mix_api.logic import (
        get_axis_escalation_target_mix,
    )


router = APIRouter(prefix="/api", tags=["axis-escalation"])


class EscalationTargetSummary(BaseModel):
    count: int
    servers: List[int]


class EscalatedAxisSummary(BaseModel):
    count: int
    avg_p_top: float
    max_p_critical: float


class AxisEscalationTargetMixResponse(BaseModel):
    escalated_to: Dict[str, EscalationTargetSummary]
    by_axis: Dict[str, EscalatedAxisSummary]
    total_escalated: int
    servers_escalated: int


@router.get(
    "/axes/escalation/targets",
    response_model=AxisEscalationTargetMixResponse,
)
def axis_escalation_targets(
    session: Session = Depends(get_session),
) -> AxisEscalationTargetMixResponse:
    return get_axis_escalation_target_mix(session)


def _run_self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpLlmAxisScore.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        McpLlmAxisScore(
            id=1,
            server_id=101,
            axis_name="overall_risk",
            label="high",
            label_index=3,
            probs={"high": 0.8},
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.3,
            escalated=True,
            escalated_to="security-review",
            decision_rule_version="v1",
            model_version="test-model",
            scored_at=now,
            adapter_sha256="a" * 64,
        ),
        McpLlmAxisScore(
            id=2,
            server_id=102,
            axis_name="auth_strength",
            label="weak",
            label_index=0,
            probs={"weak": 0.7},
            p_top=0.7,
            p_critical=0.4,
            p_danger=0.2,
            escalated=True,
            escalated_to="owner-review",
            decision_rule_version="v1",
            model_version="test-model",
            scored_at=now,
            adapter_sha256="b" * 64,
        ),
        McpLlmAxisScore(
            id=3,
            server_id=103,
            axis_name="overall_risk",
            label="medium",
            label_index=2,
            probs={"medium": 0.5},
            p_top=0.5,
            p_critical=0.1,
            p_danger=0.1,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="test-model",
            scored_at=now,
            adapter_sha256="c" * 64,
        ),
        McpLlmAxisScore(
            id=4,
            server_id=104,
            axis_name="data_sensitivity",
            label="low",
            label_index=1,
            probs={"low": 0.3},
            p_top=0.3,
            p_critical=0.0,
            p_danger=0.1,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1",
            model_version="test-model",
            scored_at=now,
            adapter_sha256="d" * 64,
        ),
    ]

    with testing_session.begin() as db:
        db.add_all(rows)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/axes/escalation/targets")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_escalated"] == 2, payload
    assert len(payload["escalated_to"]) == 2, payload
    assert len(payload["by_axis"]) >= 1, payload
    print("PASS")


if __name__ == "__main__":
    _run_self_test()