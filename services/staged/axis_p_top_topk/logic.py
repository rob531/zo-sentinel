"""logic.py -- the service's data/computation layer for ranked axis risk scores."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}

router = APIRouter(prefix="/api/axis/p-top", tags=["axis-p-top"])


def _scored_at_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/topk")
def axis_p_top_topk(
    axis: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_session),
):
    """Return the highest-p_top servers for one of the seven known axes."""
    if axis not in VALID_AXES:
        return JSONResponse(status_code=400, content={"error": "unknown_axis"})

    rows = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.scored_at,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.axis_name == axis)
        .order_by(McpLlmAxisScore.p_top.desc())
        .limit(limit)
    ).all()

    items: List[Dict[str, Any]] = [
        {
            "server_id": str(server_id),
            "name": str(name or ""),
            "risk_tier": str(risk_tier or ""),
            "p_top": float(p_top or 0.0),
            "p_critical": float(p_critical or 0.0),
            "p_danger": float(p_danger or 0.0),
            "scored_at": _scored_at_string(scored_at),
        }
        for server_id, name, risk_tier, p_top, p_critical, p_danger, scored_at in rows
    ]

    return {"axis": axis, "limit": limit, "items": items}


__all__ = ["axis_p_top_topk", "router", "VALID_AXES"]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    db.add_all(
        [
            McpServerRegistry(server_id="srv-a", name="Server A", risk_tier="high"),
            McpServerRegistry(server_id="srv-b", name="Server B", risk_tier="medium"),
            McpServerRegistry(server_id="srv-c", name="Server C", risk_tier="low"),
        ]
    )
    db.add_all(
        [
            McpLlmAxisScore(
                id=1,
                server_id="srv-a",
                axis_name="auth_strength",
                model_version="test",
                label="a",
                p_top=0.9,
                p_critical=0.8,
                p_danger=0.1,
                scored_at=datetime(2024, 1, 1),
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-b",
                axis_name="auth_strength",
                model_version="test",
                label="b",
                p_top=0.5,
                p_critical=0.4,
                p_danger=0.1,
                scored_at=datetime(2024, 1, 1),
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv-c",
                axis_name="auth_strength",
                model_version="test",
                label="c",
                p_top=0.1,
                p_critical=0.05,
                p_danger=0.05,
                scored_at=datetime(2024, 1, 1),
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv-a",
                axis_name="overall_risk",
                model_version="test",
                label="other",
                p_top=0.99,
                p_critical=0.9,
                p_danger=0.1,
                scored_at=datetime(2024, 1, 1),
            ),
        ]
    )
    db.commit()
    db.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/axis/p-top/topk", params={"axis": "auth_strength", "limit": 10})
        assert response.status_code == 200
        payload = response.json()
        assert payload["axis"] == "auth_strength"
        assert payload["limit"] == 10
        assert len(payload["items"]) == 3
        assert payload["items"][0]["server_id"] == "srv-a"
        assert payload["items"][0]["p_top"] == 0.9
        assert all(item["server_id"] != "srv-other" for item in payload["items"])

        response = client.get("/api/axis/p-top/topk", params={"axis": "not_an_axis"})
        assert response.status_code == 400
        assert response.json() == {"error": "unknown_axis"}

    print("PASS")