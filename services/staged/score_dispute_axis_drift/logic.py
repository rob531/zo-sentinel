"""Logic for the score dispute axis-drift service."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _proposed_axis_values(value: Any) -> Dict[str, float]:
    """Return proposed p_top values from the JSON representation used by the app."""
    if value is None:
        return {}

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}

    if isinstance(value, Mapping):
        result: Dict[str, float] = {}
        for axis_name, score in value.items():
            if isinstance(score, Mapping):
                score = score.get("p_top", score.get("value"))
            number = _as_float(score)
            if axis_name is not None and number is not None:
                result[str(axis_name)] = number
        return result

    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            axis_name = item.get("axis_name", item.get("name"))
            score = item.get("p_top", item.get("value"))
            number = _as_float(score)
            if axis_name is not None and number is not None:
                result[str(axis_name)] = number
        return result

    return {}


def _latest_scores(db: Session) -> Dict[str, Dict[str, McpLlmAxisScore]]:
    """Load the latest score for each server and axis."""
    rows = db.execute(
        select(McpLlmAxisScore).order_by(
            McpLlmAxisScore.scored_at.desc().nulls_last(),
            McpLlmAxisScore.id.desc(),
        )
    ).all()

    latest: Dict[str, Dict[str, McpLlmAxisScore]] = {}
    for row in rows:
        server_scores = latest.setdefault(str(row.server_id), {})
        axis_name = str(row.axis_name)
        if axis_name not in server_scores:
            server_scores[axis_name] = row
    return latest


def _age_hours(created_at: Any) -> Optional[float]:
    if created_at is None:
        return None
    if not isinstance(created_at, datetime):
        return None

    current = datetime.now(timezone.utc)
    value = created_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return max(0.0, (current - value).total_seconds() / 3600.0)


def get_axis_drift(db: Session) -> Dict[str, Any]:
    """Compute proposed-versus-current axis drift for all score disputes."""
    disputes = db.execute(
        select(McpScoreDispute).order_by(McpScoreDispute.created_at.desc())
    ).all()
    registry_rows = db.execute(select(McpServerRegistry)).all()
    latest_scores = _latest_scores(db)

    risk_tiers = {
        str(row.server_id): row.risk_tier
        for row in registry_rows
    }

    output_disputes: List[Dict[str, Any]] = []
    summary = {"open": 0, "approved": 0, "rejected": 0, "escalated": 0}

    for dispute in disputes:
        status = str(dispute.status or "").strip().lower()
        if status in summary:
            summary[status] += 1

        proposed_axes = _proposed_axis_values(dispute.proposed_axes)
        current_axes = latest_scores.get(str(dispute.server_id), {})
        axis_deltas: List[Dict[str, Any]] = []
        differences: List[float] = []

        for axis_name, proposed_p_top in proposed_axes.items():
            current_score = current_axes.get(axis_name)
            if current_score is None:
                continue
            current_p_top = _as_float(current_score.p_top)
            if current_p_top is None:
                continue
            abs_delta = abs(proposed_p_top - current_p_top)
            axis_deltas.append(
                {
                    "axis_name": axis_name,
                    "proposed_p_top": float(proposed_p_top),
                    "current_p_top": float(current_p_top),
                    "abs_delta": float(abs_delta),
                }
            )
            differences.append(float(abs_delta))

        mean_abs_delta = (
            float(sum(differences) / len(differences)) if differences else None
        )

        output_disputes.append(
            {
                "dispute_id": dispute.id,
                "server_id": dispute.server_id,
                "status": dispute.status,
                "reason_category": dispute.reason_category,
                "proposed_overall_risk": dispute.proposed_overall_risk,
                "current_risk_tier": risk_tiers.get(str(dispute.server_id)),
                "axis_deltas": axis_deltas,
                "mean_abs_delta": mean_abs_delta,
                "age_hours": _age_hours(dispute.created_at),
            }
        )

    return {
        "disputes": output_disputes,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Common names used by service routers and contracts.
compute_axis_drift = get_axis_drift
get_dispute_axis_drift = get_axis_drift
axis_drift = get_axis_drift


if __name__ == "__main__":
    from types import SimpleNamespace

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    class _SelfTestResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _SelfTestSession:
        def __init__(self):
            self._calls = 0

        def execute(self, statement):
            self._calls += 1
            if self._calls == 1:
                return _SelfTestResult(
                    [
                        SimpleNamespace(
                            id=1,
                            server_id="server-1",
                            status="open",
                            reason_category="model_disagreement",
                            proposed_overall_risk="high",
                            proposed_axes={"critical": 0.8, "danger": 0.5},
                            created_at=datetime.now(timezone.utc),
                        )
                    ]
                )
            if self._calls == 2:
                return _SelfTestResult(
                    [
                        SimpleNamespace(server_id="server-1", risk_tier="medium"),
                        SimpleNamespace(server_id="server-2", risk_tier="low"),
                    ]
                )
            return _SelfTestResult(
                [
                    SimpleNamespace(
                        server_id="server-1",
                        axis_name="critical",
                        p_top=0.7,
                        scored_at=datetime.now(timezone.utc),
                        id=2,
                    ),
                    SimpleNamespace(
                        server_id="server-1",
                        axis_name="danger",
                        p_top=0.5,
                        scored_at=datetime.now(timezone.utc),
                        id=1,
                    ),
                ]
            )

    self_test_app = FastAPI()
    self_test_session = _SelfTestSession()

    def self_test_get_session():
        yield self_test_session

    self_test_app.dependency_overrides[get_session] = self_test_get_session

    @self_test_app.get("/api/disputes/axis-drift")
    def self_test_axis_drift(db: Session = Depends(get_session)):
        return get_axis_drift(db)

    response = TestClient(self_test_app).get("/api/disputes/axis-drift")
    body = response.json()
    assert response.status_code == 200
    assert len(body["disputes"]) == 1
    assert len(body["disputes"][0]["axis_deltas"]) == 2
    assert isinstance(body["disputes"][0]["axis_deltas"][0]["abs_delta"], float)
    assert body["disputes"][0]["axis_deltas"][0]["abs_delta"] >= 0
    assert body["summary"]["open"] == 1
    print("PASS")