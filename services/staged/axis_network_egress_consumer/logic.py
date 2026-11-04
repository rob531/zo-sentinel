from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXIS_NAME = "network_egress"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def risk_tier_for(p_top: Optional[float], p_critical: Optional[float]) -> str:
    """Map axis probabilities to the effective server risk tier."""
    critical = float(p_critical or 0.0)
    top = float(p_top or 0.0)
    if critical >= 0.5:
        return "HIGH"
    if top >= 0.6:
        return "HIGH"
    if top >= 0.3:
        return "MEDIUM"
    return "LOW"


def get_network_egress_scores(
    db: Session,
    server_id: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Return network-egress scores with registry names and effective tiers."""
    bounded_limit = max(0, min(int(limit), MAX_LIMIT))
    statement = (
        select(
            McpLlmAxisScore,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            isouter=True,
        )
        .where(McpLlmAxisScore.axis_name == AXIS_NAME)
        .order_by(McpLlmAxisScore.p_top.desc().nullslast())
        .limit(bounded_limit)
    )
    if server_id is not None:
        statement = statement.where(McpLlmAxisScore.server_id == server_id)

    rows = []
    for score, name, registry_risk_tier, trust_score in db.execute(statement).all():
        rows.append(
            {
                "server_id": score.server_id,
                "name": name,
                "label": score.label,
                "label_index": score.label_index,
                "p_top": float(score.p_top) if score.p_top is not None else None,
                "p_critical": (
                    float(score.p_critical) if score.p_critical is not None else None
                ),
                "p_danger": (
                    float(score.p_danger) if score.p_danger is not None else None
                ),
                "escalated": bool(score.escalated) if score.escalated is not None else False,
                "escalated_to": score.escalated_to,
                "decision_rule_version": score.decision_rule_version,
                "model_version": score.model_version,
                "scored_at": score.scored_at,
                "risk_tier": risk_tier_for(score.p_top, score.p_critical),
            }
        )

    return {"axis": AXIS_NAME, "rows": rows, "count": len(rows)}


get_network_egress_rows = get_network_egress_scores
fetch_network_egress_scores = get_network_egress_scores
list_network_egress_scores = get_network_egress_scores
get_axis_scores = get_network_egress_scores


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="egress-alpha",
                    name="Egress Alpha",
                    risk_tier="MEDIUM",
                    trust_score=80.0,
                ),
                McpServerRegistry(
                    server_id="egress-beta",
                    name="Egress Beta",
                    risk_tier="LOW",
                    trust_score=70.0,
                ),
                McpServerRegistry(
                    server_id="egress-gamma",
                    name="Egress Gamma",
                    risk_tier="HIGH",
                    trust_score=90.0,
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="egress-alpha",
                    axis_name=AXIS_NAME,
                    label="HIGH",
                    label_index=2,
                    p_top=0.91,
                    p_critical=0.10,
                    p_danger=0.70,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="egress-beta",
                    axis_name=AXIS_NAME,
                    label="CRITICAL",
                    label_index=3,
                    p_top=0.22,
                    p_critical=0.82,
                    p_danger=0.60,
                    escalated=True,
                    escalated_to="security",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="egress-gamma",
                    axis_name=AXIS_NAME,
                    label="MEDIUM",
                    label_index=1,
                    p_top=0.45,
                    p_critical=0.15,
                    p_danger=0.30,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/axis/network_egress")
    def network_egress_endpoint(db: Session = Depends(get_session)):
        return get_network_egress_scores(db)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/network_egress")
        assert response.status_code == 200
        payload = response.json()
        assert payload["axis"] == AXIS_NAME
        assert payload["count"] == 3
        assert len(payload["rows"]) == 3
        assert payload["rows"][0]["p_top"] == 0.91
        critical_row = next(
            row for row in payload["rows"] if row["server_id"] == "egress-beta"
        )
        assert critical_row["risk_tier"] == "HIGH"

    print("PASS")