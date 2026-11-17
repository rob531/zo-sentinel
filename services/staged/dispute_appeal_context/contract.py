from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

router = APIRouter()


def _proposed_axes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = __import__("json").loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _proposed_label(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("label")
    return value


@router.get("/disputes/{dispute_id}/appeal-context")
def get_dispute_appeal_context(
    dispute_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    row = session.execute(
        select(McpScoreDispute, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.id == dispute_id)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Dispute not found")

    dispute, server = row
    axis_rows = session.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == dispute.server_id)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
    ).scalars().all()

    current_axes: dict[str, dict[str, Any]] = {}
    for axis in axis_rows:
        if axis.axis_name in current_axes:
            continue
        current_axes[axis.axis_name] = {
            "label": axis.label,
            "p_top": axis.p_top,
            "p_critical": axis.p_critical,
            "p_danger": axis.p_danger,
            "escalated": axis.escalated,
        }

    proposed_axes = _proposed_axes(dispute.proposed_axes)
    diff: dict[str, dict[str, Any]] = {}
    for axis_name, current in current_axes.items():
        if axis_name == "overall_risk":
            proposed_label = dispute.proposed_overall_risk
        else:
            proposed_label = _proposed_label(proposed_axes.get(axis_name))
        diff[axis_name] = {
            "current_label": current["label"],
            "proposed_label": proposed_label,
            "changed": current["label"] != proposed_label,
        }

    status = (dispute.status or "").lower()
    resolution_ready = (
        status not in {"approved", "closed", "rejected", "resolved"}
        and "overall_risk" in current_axes
    )

    return {
        "dispute": {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "status": dispute.status,
            "reason_category": dispute.reason_category,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "proposed_axes": proposed_axes,
            "explanation": dispute.explanation,
            "admin_note": dispute.admin_note,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at,
        },
        "current": {
            "name": server.name,
            "verdict": server.verdict,
            "risk_tier": server.risk_tier,
            "trust_score": server.trust_score,
            "axes": current_axes,
        },
        "diff": diff,
        "resolution_ready": bool(resolution_ready),
    }


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)

    session = TestingSessionLocal()
    session.add(
        McpServerRegistry(
            server_id="server-1",
            name="Example MCP Server",
            url="https://example.invalid/mcp",
            verdict="approved",
            risk_tier="low",
            trust_score=0.91,
        )
    )

    axis_values = [
        ("overall_risk", "low", 0.12, 0.01, 0.02, False),
        ("correctness", "medium", 0.71, 0.12, 0.08, False),
        ("safety", "low", 0.16, 0.02, 0.03, False),
        ("privacy", "low", 0.14, 0.02, 0.01, False),
        ("reliability", "high", 0.84, 0.31, 0.22, True),
        ("transparency", "medium", 0.58, 0.09, 0.06, False),
        ("robustness", "medium", 0.62, 0.14, 0.09, False),
    ]
    for axis_id, (axis_name, label, p_top, p_critical, p_danger, escalated) in enumerate(
        axis_values, start=1
    ):
        session.add(
            McpLlmAxisScore(
                id=axis_id,
                server_id="server-1",
                axis_name=axis_name,
                label=label,
                p_top=p_top,
                p_critical=p_critical,
                p_danger=p_danger,
                escalated=escalated,
                model_version="model-current",
            )
        )

    session.add(
        McpScoreDispute(
            id=42,
            server_id="server-1",
            submitted_by="reviewer@example.test",
            proposed_overall_risk="high",
            proposed_axes={
                "correctness": {"label": "medium"},
                "safety": {"label": "low"},
                "privacy": {"label": "low"},
                "reliability": {"label": "low"},
                "transparency": {"label": "medium"},
                "robustness": {"label": "medium"},
            },
            reason_category="model_behavior",
            explanation="The proposed appeal uses a different overall assessment.",
            status="pending",
        )
    )
    session.commit()
    session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/42/appeal-context")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["dispute"]["id"] == 42
        assert "overall_risk" in payload["current"]["axes"]
        assert any(axis["changed"] for axis in payload["diff"].values())
        assert isinstance(payload["resolution_ready"], bool)

    print("PASS")


if __name__ == "__main__":
    main()