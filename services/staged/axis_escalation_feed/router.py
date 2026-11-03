"""HTTP surface for the axis escalation feed."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_escalations, get_server_escalations

router = APIRouter(prefix="/api/axis", tags=["axis"])


@router.get("/escalations")
def list_escalations(db: Session = Depends(get_session)) -> dict[str, Any]:
    return get_escalations(db)


@router.get("/escalations/{server_id}")
def server_escalations(
    server_id: str, db: Session = Depends(get_session)
) -> dict[str, Any]:
    result = get_server_escalations(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Server One"),
                McpServerRegistry(server_id="server-2", name="Server Two"),
                McpServerRegistry(server_id="server-3", name="Server Three"),
            ]
        )
        scored_at = datetime.now(timezone.utc)
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="security",
                    label="critical",
                    label_index=3,
                    p_top=0.95,
                    p_critical=0.95,
                    p_danger=0.04,
                    escalated=True,
                    escalated_to="security",
                    decision_rule_version="v1",
                    model_version="model-1",
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="quality",
                    label="warning",
                    label_index=2,
                    p_top=0.88,
                    p_critical=0.12,
                    p_danger=0.55,
                    escalated=True,
                    escalated_to="quality",
                    decision_rule_version="v1",
                    model_version="model-1",
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="security",
                    label="normal",
                    label_index=1,
                    p_top=0.75,
                    p_critical=0.05,
                    p_danger=0.20,
                    escalated=False,
                    model_version="model-1",
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-1",
                    axis_name="quality",
                    label="normal",
                    label_index=1,
                    p_top=0.70,
                    p_critical=0.03,
                    p_danger=0.27,
                    escalated=False,
                    model_version="model-1",
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="server-2",
                    axis_name="security",
                    label="normal",
                    label_index=1,
                    p_top=0.68,
                    p_critical=0.02,
                    p_danger=0.30,
                    escalated=False,
                    model_version="model-1",
                    scored_at=scored_at,
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

    with TestClient(test_app) as client:
        response = client.get("/api/axis/escalations")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert sum(payload["axis_counts"].values()) == 2

        response = client.get("/api/axis/escalations/server-1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["escalated_axes"][0]["axis_name"] == "security"

        response = client.get("/api/axis/escalations/unknown-server")
        assert response.status_code == 404

    print("PASS")