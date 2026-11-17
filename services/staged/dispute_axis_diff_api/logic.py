from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute


router = APIRouter(prefix="/api", tags=["disputes"])


class AxisDiff(BaseModel):
    axis_name: str
    proposed_label: Optional[str]
    current_label: Optional[str]
    proposed_p_top: Optional[float]
    current_p_top: Optional[float]
    delta_p_top: Optional[float]
    agrees: bool


class OverallDiff(BaseModel):
    proposed_overall_risk: Optional[str]
    current_overall_risk: Optional[str]
    agrees: bool


class AxisDiffSummary(BaseModel):
    axes_compared: int
    axes_disagreeing: int


class AxisDiffResponse(BaseModel):
    dispute_id: int
    server_id: str
    status: Optional[str]
    reason_category: Optional[str]
    created_at: Optional[datetime]
    resolved_at: Optional[datetime]
    axes: List[AxisDiff]
    overall: OverallDiff
    summary: AxisDiffSummary


def _proposed_axes_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _proposed_axis_details(value: Any) -> tuple[Optional[str], Optional[float]]:
    if isinstance(value, dict):
        label = value.get("proposed_label") or value.get("label")
        p_top = value.get("proposed_p_top")
        if p_top is None:
            p_top = value.get("p_top")
        try:
            p_top = float(p_top) if p_top is not None else None
        except (TypeError, ValueError):
            p_top = None
        return (str(label) if label is not None else None), p_top

    label = str(value) if value is not None else None
    return label, None


def _probability_for_label(probs: Any, label: Optional[str]) -> Optional[float]:
    if label is None:
        return None

    def as_float(value: Any) -> Optional[float]:
        if isinstance(value, dict):
            value = value.get("p_top", value.get("probability", value.get("p")))
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    if isinstance(probs, dict):
        if label in probs:
            return as_float(probs[label])
        for key, value in probs.items():
            if str(key).casefold() == label.casefold():
                return as_float(value)

    if isinstance(probs, list):
        for item in probs:
            if isinstance(item, dict):
                item_label = item.get("label") or item.get("name")
                if item_label is not None and str(item_label).casefold() == label.casefold():
                    return as_float(item)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                if str(item[0]).casefold() == label.casefold():
                    return as_float(item[1])

    return None


def compute_axis_diff(
    dispute_id: int,
    session: Session,
) -> AxisDiffResponse:
    dispute = session.get(McpScoreDispute, dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id} not found",
        )

    proposed_axes = _proposed_axes_mapping(dispute.proposed_axes)
    axis_names = [str(axis_name) for axis_name in proposed_axes.keys()]

    score_filters = []
    if axis_names:
        score_filters.append(McpLlmAxisScore.axis_name.in_(axis_names))
    score_filters.append(McpLlmAxisScore.axis_name == "overall_risk")

    scores = session.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == dispute.server_id)
        .where(or_(*score_filters))
        .order_by(
            McpLlmAxisScore.scored_at.is_(None),
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    ).scalars().all()

    current_by_axis: Dict[str, McpLlmAxisScore] = {}
    for score in scores:
        current_by_axis.setdefault(score.axis_name, score)

    axis_results: List[AxisDiff] = []
    for axis_name, proposed_value in proposed_axes.items():
        proposed_label, explicit_p_top = _proposed_axis_details(proposed_value)
        current_score = current_by_axis.get(str(axis_name))
        current_label = current_score.label if current_score is not None else None
        current_p_top = current_score.p_top if current_score is not None else None

        proposed_p_top = explicit_p_top
        if proposed_p_top is None and current_score is not None:
            proposed_p_top = _probability_for_label(
                current_score.probs,
                proposed_label,
            )
        if proposed_p_top is None and current_score is not None:
            proposed_p_top = current_p_top if proposed_label == current_label else 0.0

        delta_p_top = (
            proposed_p_top - current_p_top
            if proposed_p_top is not None and current_p_top is not None
            else None
        )

        axis_results.append(
            AxisDiff(
                axis_name=str(axis_name),
                proposed_label=proposed_label,
                current_label=current_label,
                proposed_p_top=proposed_p_top,
                current_p_top=current_p_top,
                delta_p_top=delta_p_top,
                agrees=proposed_label == current_label,
            )
        )

    overall_score = current_by_axis.get("overall_risk")
    current_overall_risk = overall_score.label if overall_score is not None else None

    return AxisDiffResponse(
        dispute_id=dispute.id,
        server_id=dispute.server_id,
        status=dispute.status,
        reason_category=dispute.reason_category,
        created_at=dispute.created_at,
        resolved_at=dispute.resolved_at,
        axes=axis_results,
        overall=OverallDiff(
            proposed_overall_risk=dispute.proposed_overall_risk,
            current_overall_risk=current_overall_risk,
            agrees=dispute.proposed_overall_risk == current_overall_risk,
        ),
        summary=AxisDiffSummary(
            axes_compared=len(axis_results),
            axes_disagreeing=sum(1 for axis in axis_results if not axis.agrees),
        ),
    )


@router.get(
    "/disputes/{dispute_id}/axis-diff",
    response_model=AxisDiffResponse,
)
def get_dispute_axis_diff(
    dispute_id: int,
    session: Session = Depends(get_session),
) -> AxisDiffResponse:
    return compute_axis_diff(dispute_id, session)


def _self_test() -> None:
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
    McpScoreDispute.__table__.create(bind=engine)
    McpLlmAxisScore.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
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
                explanation="Self-test dispute",
                status="open",
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
                    label="RISK_ACCEPTED",
                    label_index=0,
                    adapter_sha256="test-adapter",
                    model_version="test-model",
                    decision_rule_version="test-rule",
                    p_critical=0.01,
                    p_danger=0.04,
                    p_top=0.95,
                    probs={"RISK_ACCEPTED": 0.95},
                    escalated=False,
                    escalated_to=None,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-1",
                    axis_name="auth_strength",
                    label="strong",
                    label_index=1,
                    adapter_sha256="test-adapter",
                    model_version="test-model",
                    decision_rule_version="test-rule",
                    p_critical=0.01,
                    p_danger=0.09,
                    p_top=0.90,
                    probs={"weak": 0.10, "strong": 0.90},
                    escalated=False,
                    escalated_to=None,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-1",
                    axis_name="network_egress",
                    label="high",
                    label_index=1,
                    adapter_sha256="test-adapter",
                    model_version="test-model",
                    decision_rule_version="test-rule",
                    p_critical=0.01,
                    p_danger=0.19,
                    p_top=0.80,
                    probs={"low": 0.20, "high": 0.80},
                    escalated=False,
                    escalated_to=None,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/1/axis-diff")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["axes_compared"] == 2
        assert payload["summary"]["axes_disagreeing"] == 1
        assert payload["overall"]["agrees"] is False

        missing = client.get("/api/disputes/999/axis-diff")
        assert missing.status_code == 404, missing.text
        assert "not found" in missing.json()["detail"].lower()

    print("PASS")


if __name__ == "__main__":
    _self_test()