from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


KNOWN_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}

router = APIRouter(prefix="/api")
api_router = router


def _validate_axis(axis: str) -> str:
    if axis not in KNOWN_AXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown axis: {axis}",
        )
    return axis


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def list_axis_evidence(
    db: Session,
    axis: str = "overall_risk",
    limit: int = 50,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Return axis evidence from the real application tables."""
    axis = _validate_axis(axis)
    limit = max(1, min(int(limit), 200))

    count_statement = (
        select(func.count(McpLlmAxisScore.id))
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis)
    )
    if registry_source is not None:
        count_statement = count_statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    total = int(db.execute(count_statement).scalar_one())

    statement = (
        select(
            McpLlmAxisScore,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.registry_source,
        )
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis)
        .order_by(
            McpLlmAxisScore.p_top.desc(),
            McpLlmAxisScore.server_id.asc(),
        )
        .limit(limit)
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = db.execute(statement).all()
    items: List[Dict[str, Any]] = []
    for row in rows:
        score = row[0]
        items.append(
            {
                "server_id": str(score.server_id),
                "name": row[1],
                "risk_tier": row[2],
                "registry_source": row[3],
                "label": score.label,
                "p_top": float(score.p_top or 0.0),
                "p_critical": float(score.p_critical or 0.0),
                "p_danger": float(score.p_danger or 0.0),
                "escalated": bool(score.escalated),
                "escalated_to": score.escalated_to,
                "decision_rule_version": score.decision_rule_version,
                "model_version": score.model_version,
                "scored_at": _stringify(score.scored_at),
            }
        )

    return {
        "axis": axis,
        "total": total,
        "items": items,
    }


def get_axis_evidence(
    db: Session,
    axis: str = "overall_risk",
    limit: int = 50,
    registry_source: Optional[str] = None,
) -> Dict[str, Any]:
    return list_axis_evidence(db, axis, limit, registry_source)


@router.get("/axes/evidence")
def axis_evidence(
    axis: str = Query(default="overall_risk"),
    limit: int = Query(default=50),
    registry_source: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return list_axis_evidence(db, axis, limit, registry_source)


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
    McpLlmAxisScore.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a",
                    name="Server A",
                    risk_tier="high",
                    registry_source="seed",
                ),
                McpServerRegistry(
                    server_id="server-b",
                    name="Server B",
                    risk_tier="medium",
                    registry_source="seed",
                ),
                McpServerRegistry(
                    server_id="server-c",
                    name="Server C",
                    risk_tier="low",
                    registry_source="seed",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name="overall_risk",
                    label="critical",
                    p_top=0.95,
                    p_critical=0.8,
                    p_danger=0.1,
                    escalated=True,
                    escalated_to="security",
                    decision_rule_version="v1",
                    model_version="m1",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-b",
                    axis_name="overall_risk",
                    label="high",
                    p_top=0.75,
                    p_critical=0.4,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="m1",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-c",
                    axis_name="overall_risk",
                    label="low",
                    p_top=0.25,
                    p_critical=0.1,
                    p_danger=0.05,
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="m1",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-a",
                    axis_name="auth_strength",
                    label="strong",
                    p_top=0.99,
                    p_critical=0.2,
                    p_danger=0.1,
                    escalated=False,
                    decision_rule_version="v1",
                    model_version="m1",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get(
        "/api/axes/evidence",
        params={"axis": "overall_risk"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["axis"] == "overall_risk"
    assert payload["total"] == 3
    assert len(payload["items"]) == 3
    assert payload["items"][0]["p_top"] == 0.95
    assert [item["p_top"] for item in payload["items"]] == [0.95, 0.75, 0.25]
    print("PASS")