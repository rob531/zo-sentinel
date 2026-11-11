from typing import Any, Dict, Mapping, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


def _decode_proposed_axes(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _proposed_label(proposed_axes: Any, axis_name: str) -> Optional[str]:
    if not isinstance(proposed_axes, Mapping):
        return None

    value = proposed_axes.get(axis_name)
    if isinstance(value, Mapping):
        value = value.get("label")
    if value is None:
        return None
    return str(value)


def get_appeal_context(
    dispute_id: int,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    dispute_row = session.execute(
        select(
            McpScoreDispute.id,
            McpScoreDispute.server_id,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.proposed_axes,
            McpScoreDispute.explanation,
            McpScoreDispute.admin_note,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.id == dispute_id)
    ).mappings().one_or_none()

    if dispute_row is None:
        raise HTTPException(status_code=404, detail="Dispute not found")

    axis_rows = session.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.escalated,
        )
        .where(McpLlmAxisScore.server_id == dispute_row["server_id"])
        .order_by(
            McpLlmAxisScore.scored_at.desc(),
            McpLlmAxisScore.id.desc(),
        )
    ).mappings().all()

    axes: Dict[str, Dict[str, Any]] = {}
    for row in axis_rows:
        axis_name = str(row["axis_name"])
        if axis_name in axes:
            continue
        axes[axis_name] = {
            "label": row["label"],
            "p_top": row["p_top"],
            "p_critical": row["p_critical"],
            "p_danger": row["p_danger"],
            "escalated": row["escalated"],
        }

    proposed_axes = _decode_proposed_axes(dispute_row["proposed_axes"])
    diff: Dict[str, Dict[str, Any]] = {}

    for axis_name, current in axes.items():
        if axis_name == "overall_risk":
            proposed_label = dispute_row["proposed_overall_risk"]
        else:
            proposed_label = _proposed_label(proposed_axes, axis_name)

        proposed_label = (
            str(proposed_label) if proposed_label is not None else None
        )
        current_label = (
            str(current["label"]) if current["label"] is not None else None
        )
        diff[axis_name] = {
            "current_label": current_label,
            "proposed_label": proposed_label,
            "changed": bool(
                proposed_label is not None and current_label != proposed_label
            ),
        }

    status = dispute_row["status"]
    resolution_ready = bool(
        dispute_row["proposed_overall_risk"] is not None
        and "overall_risk" in axes
        and status not in {"resolved", "closed", "rejected"}
    )

    return {
        "dispute": {
            "id": dispute_row["id"],
            "server_id": dispute_row["server_id"],
            "status": status,
            "reason_category": dispute_row["reason_category"],
            "proposed_overall_risk": dispute_row["proposed_overall_risk"],
            "proposed_axes": proposed_axes,
            "explanation": dispute_row["explanation"],
            "admin_note": dispute_row["admin_note"],
            "created_at": dispute_row["created_at"],
            "resolved_at": dispute_row["resolved_at"],
        },
        "current": {
            "name": dispute_row["name"],
            "url": dispute_row["url"],
            "verdict": dispute_row["verdict"],
            "risk_tier": dispute_row["risk_tier"],
            "trust_score": dispute_row["trust_score"],
            "axes": axes,
        },
        "diff": diff,
        "resolution_ready": resolution_ready,
    }


get_dispute_appeal_context = get_appeal_context
build_appeal_context = get_appeal_context


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
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)

    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    seeded_session = TestingSession()

    seeded_session.add(
        McpServerRegistry(
            server_id="server-self-test",
            name="Self-test server",
            url="https://example.invalid/mcp",
            verdict="review",
            risk_tier="high",
            trust_score=72.5,
        )
    )
    seeded_session.flush()

    axis_names = [
        "overall_risk",
        "code_execution",
        "data_exfiltration",
        "credential_access",
        "network_access",
        "tool_poisoning",
        "prompt_injection",
    ]
    for index, axis_name in enumerate(axis_names):
        seeded_session.add(
            McpLlmAxisScore(
                id=index + 1,
                server_id="server-self-test",
                axis_name=axis_name,
                label="high" if axis_name == "overall_risk" else "low",
                p_top=0.8 if axis_name == "overall_risk" else 0.1,
                p_critical=0.1 if axis_name == "overall_risk" else 0.05,
                p_danger=0.1 if axis_name == "overall_risk" else 0.05,
                escalated=axis_name == "overall_risk",
                model_version="self-test",
            )
        )

    seeded_session.add(
        McpScoreDispute(
            id=1,
            server_id="server-self-test",
            submitted_by="self-test-user",
            proposed_overall_risk="medium",
            proposed_axes={"overall_risk": "medium"},
            reason_category="model_error",
            explanation="The current overall risk label should be reconsidered.",
            status="open",
        )
    )
    seeded_session.commit()
    seeded_session.close()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/{dispute_id}/appeal-context")
    def appeal_context_endpoint(
        dispute_id: int,
        session: Session = Depends(get_session),
    ):
        return get_appeal_context(dispute_id, session)

    response = TestClient(test_app).get(
        "/api/disputes/1/appeal-context"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dispute"]["id"] == 1
    assert "overall_risk" in payload["current"]["axes"]
    assert any(
        item["changed"] for item in payload["diff"].values()
    )
    assert isinstance(payload["resolution_ready"], bool)
    print("PASS")