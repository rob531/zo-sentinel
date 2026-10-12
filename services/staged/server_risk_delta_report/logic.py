from app.db import get_session
from app.models import McpLlmAxisScore
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel


class AxisDelta(BaseModel):
    current_p_top: float
    previous_p_top: float
    delta: float
    direction: str


class ServerRiskDeltaResponse(BaseModel):
    server_id: str
    current_tier: str
    previous_tier: str
    tier_changed: bool
    axes: dict[str, AxisDelta]
    overall_delta: float


def get_tier_from_p_top(p_top: float) -> str:
    if p_top >= 0.8:
        return "CRITICAL"
    elif p_top >= 0.6:
        return "HIGH"
    elif p_top >= 0.4:
        return "MEDIUM"
    elif p_top >= 0.2:
        return "LOW"
    return "MINIMAL"


def get_risk_delta(session, server_id: str) -> Optional[ServerRiskDeltaResponse]:
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(2)
        .all()
    )

    if len(scores) < 2:
        return None

    current_scores = {s.axis_name: s for s in scores if s.scored_at == scores[0].scored_at}
    previous_scores = {s.axis_name: s for s in scores if s.scored_at != scores[0].scored_at}

    if not current_scores or not previous_scores:
        return None

    all_axes = set(current_scores.keys()) | set(previous_scores.keys())
    axes_data = {}
    delta_sum = 0.0
    count = 0

    for axis_name in all_axes:
        current_p_top = current_scores.get(axis_name)
        previous_p_top = previous_scores.get(axis_name)

        if current_p_top and previous_p_top:
            c_val = current_p_top.p_top
            p_val = previous_p_top.p_top
            delta = round(c_val - p_val, 6)
        elif current_p_top:
            c_val = current_p_top.p_top
            p_val = 0.0
            delta = round(c_val, 6)
        elif previous_p_top:
            c_val = 0.0
            p_val = previous_p_top.p_top
            delta = round(-p_val, 6)
        else:
            continue

        direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
        axes_data[axis_name] = AxisDelta(
            current_p_top=c_val, previous_p_top=p_val, delta=delta, direction=direction
        )
        delta_sum += delta
        count += 1

    current_tier = get_tier_from_p_top(scores[0].p_top)
    previous_tier = get_tier_from_p_top(scores[-1].p_top)
    tier_changed = current_tier != previous_tier
    overall_delta = round(delta_sum / count, 6) if count > 0 else 0.0

    return ServerRiskDeltaResponse(
        server_id=server_id,
        current_tier=current_tier,
        previous_tier=previous_tier,
        tier_changed=tier_changed,
        axes=axes_data,
        overall_delta=overall_delta,
    )


async def self_test():
    from app.main import app
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    client = TestClient(app)

    with app.container.db.session() as session:
        session.execute(text("DELETE FROM mcp_llm_axis_scores WHERE server_id = 'delta-test-server'"))

    seed_time = datetime.now(timezone.utc) - timedelta(hours=2)
    snapshot_data = [
        {
            "server_id": "delta-test-server",
            "axis_name": "security",
            "p_top": 0.75,
            "scored_at": seed_time,
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1",
            "escalated": False,
            "model_version": "test",
        },
        {
            "server_id": "delta-test-server",
            "axis_name": "reliability",
            "p_top": 0.45,
            "scored_at": seed_time,
            "adapter_sha256": "abc123",
            "decision_rule_version": "v1",
            "escalated": False,
            "model_version": "test",
        },
    ]

    for row in snapshot_data:
        session = app.container.db.session()
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, p_top, scored_at, adapter_sha256, decision_rule_version, escalated, model_version)
                VALUES (:server_id, :axis_name, :p_top, :scored_at, :adapter_sha256, :decision_rule_version, :escalated, :model_version)
            """),
            row,
        )
        session.commit()

    current_time = datetime.now(timezone.utc)
    current_data = [
        {
            "server_id": "delta-test-server",
            "axis_name": "security",
            "p_top": 0.85,
            "scored_at": current_time,
            "adapter_sha256": "def456",
            "decision_rule_version": "v1",
            "escalated": False,
            "model_version": "test",
        },
        {
            "server_id": "delta-test-server",
            "axis_name": "reliability",
            "p_top": 0.55,
            "scored_at": current_time,
            "adapter_sha256": "def456",
            "decision_rule_version": "v1",
            "escalated": False,
            "model_version": "test",
        },
    ]

    for row in current_data:
        session = app.container.db.session()
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, p_top, scored_at, adapter_sha256, decision_rule_version, escalated, model_version)
                VALUES (:server_id, :axis_name, :p_top, :scored_at, :adapter_sha256, :decision_rule_version, :escalated, :model_version)
            """),
            row,
        )
        session.commit()

    response = client.get("/api/servers/delta-test-server/risk-delta")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert isinstance(data["tier_changed"], bool), f"tier_changed should be bool, got {type(data['tier_changed'])}"

    has_nonzero = any(abs(ax["delta"]) > 0 for ax in data["axes"].values())
    assert has_nonzero, "At least one axis delta should be non-zero"

    print("PASS")