from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute

try:
    from .logic import get_axis_diff as _logic_get_axis_diff
except (ImportError, ValueError):
    try:
        from .logic import get_dispute_axis_diff as _logic_get_axis_diff
    except (ImportError, ValueError):
        _logic_get_axis_diff = None


router = APIRouter(prefix="/api", tags=["dispute-axis-diff"])


class AxisDiffAxis(BaseModel):
    axis_name: str
    proposed_label: Optional[str]
    current_label: Optional[str]
    proposed_p_top: Optional[float]
    current_p_top: Optional[float]
    delta_p_top: Optional[float]
    agrees: bool


class AxisDiffOverall(BaseModel):
    proposed_overall_risk: Optional[str]
    current_overall_risk: Optional[str]
    agrees: bool


class AxisDiffSummary(BaseModel):
    axes_compared: int
    axes_disagreeing: int


class AxisDiffResponse(BaseModel):
    dispute_id: int
    server_id: str
    status: str
    reason_category: Optional[str]
    created_at: Optional[datetime]
    resolved_at: Optional[datetime]
    axes: List[AxisDiffAxis]
    overall: AxisDiffOverall
    summary: AxisDiffSummary


def _optional_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text.upper() if text else None


def _proposed_axis_values(value: Any) -> tuple[Optional[str], Optional[float]]:
    if isinstance(value, dict):
        label = value.get("label")
        if label is None:
            label = value.get("proposed_label")
        if label is None:
            label = value.get("value")
        p_top = value.get("p_top")
        if p_top is None:
            p_top = value.get("proposed_p_top")
        return (
            str(label) if label is not None else None,
            _optional_float(p_top),
        )
    if value is None:
        return None, None
    return str(value), None


def _build_axis_diff(session: Any, dispute_id: int) -> AxisDiffResponse:
    dispute = (
        session.query(McpScoreDispute)
        .filter(McpScoreDispute.id == dispute_id)
        .first()
    )
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id} not found",
        )

    score_rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == dispute.server_id)
        .order_by(
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
        .all()
    )

    current_by_axis: Dict[str, McpLlmAxisScore] = {}
    for row in score_rows:
        if row.axis_name not in current_by_axis:
            current_by_axis[row.axis_name] = row

    proposed_axes = dispute.proposed_axes or {}
    if not isinstance(proposed_axes, dict):
        proposed_axes = {}

    axes: List[AxisDiffAxis] = []
    for axis_name, proposed_value in proposed_axes.items():
        proposed_label, proposed_p_top = _proposed_axis_values(proposed_value)
        current_row = current_by_axis.get(axis_name)
        current_label = current_row.label if current_row is not None else None
        current_p_top = current_row.p_top if current_row is not None else None

        if proposed_p_top is not None and current_p_top is not None:
            delta_p_top = current_p_top - proposed_p_top
        else:
            delta_p_top = None

        normalized_proposed = _normalized_label(proposed_label)
        normalized_current = _normalized_label(current_label)
        agrees = normalized_proposed == normalized_current

        axes.append(
            AxisDiffAxis(
                axis_name=str(axis_name),
                proposed_label=proposed_label,
                current_label=current_label,
                proposed_p_top=proposed_p_top,
                current_p_top=current_p_top,
                delta_p_top=delta_p_top,
                agrees=agrees,
            )
        )

    overall_row = current_by_axis.get("overall_risk")
    current_overall_risk = overall_row.label if overall_row is not None else None
    overall_agrees = (
        _normalized_label(dispute.proposed_overall_risk)
        == _normalized_label(current_overall_risk)
    )

    return AxisDiffResponse(
        dispute_id=dispute.id,
        server_id=dispute.server_id,
        status=dispute.status,
        reason_category=dispute.reason_category,
        created_at=dispute.created_at,
        resolved_at=dispute.resolved_at,
        axes=axes,
        overall=AxisDiffOverall(
            proposed_overall_risk=dispute.proposed_overall_risk,
            current_overall_risk=current_overall_risk,
            agrees=overall_agrees,
        ),
        summary=AxisDiffSummary(
            axes_compared=len(axes),
            axes_disagreeing=sum(1 for axis in axes if not axis.agrees),
        ),
    )


@router.get(
    "/disputes/{dispute_id}/axis-diff",
    response_model=AxisDiffResponse,
)
def get_dispute_axis_diff(
    dispute_id: int,
    session: Any = Depends(get_session),
) -> AxisDiffResponse:
    return _build_axis_diff(session, dispute_id)


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = TestingSession()
    session.add(
        McpScoreDispute(
            id=1,
            server_id="server-1",
            submitted_by="tester",
            proposed_overall_risk="HIGH_RISK_ISOLATED",
            proposed_axes={
                "auth_strength": "weak",
                "network_egress": "high",
            },
            reason_category="model_disagreement",
            explanation="test dispute",
            status="OPEN",
            admin_note=None,
            created_at=now,
            resolved_at=None,
        )
    )
    session.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="server-1",
                axis_name="overall_risk",
                label="LOW_RISK",
                p_top=0.2,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                scored_at=now,
                adapter_sha256="test",
                label_index=0,
                model_version="test",
                p_critical=0.0,
                p_danger=0.0,
                probs={},
            ),
            McpLlmAxisScore(
                id=2,
                server_id="server-1",
                axis_name="auth_strength",
                label="weak",
                p_top=0.4,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                scored_at=now,
                adapter_sha256="test",
                label_index=0,
                model_version="test",
                p_critical=0.0,
                p_danger=0.0,
                probs={},
            ),
            McpLlmAxisScore(
                id=3,
                server_id="server-1",
                axis_name="network_egress",
                label="low",
                p_top=0.3,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                scored_at=now,
                adapter_sha256="test",
                label_index=0,
                model_version="test",
                p_critical=0.0,
                p_danger=0.0,
                probs={},
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        test_session = TestingSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/1/axis-diff")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["axes_compared"] == 2, payload
        assert payload["summary"]["axes_disagreeing"] == 1, payload
        assert payload["overall"]["agrees"] is False, payload

        missing = client.get("/api/disputes/999/axis-diff")
        assert missing.status_code == 404, missing.text
        assert "999" in missing.json()["detail"], missing.text

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()