from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_evidence_bundle

router = APIRouter(prefix="/api", tags=["axis-evidence"])

AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisEvidenceResponse(BaseModel):
    axis_name: AxisName
    label: str | None
    label_index: int | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    escalated: bool | None
    escalated_to: str | None
    probs: Any


class AxisEvidenceBundleResponse(BaseModel):
    server_id: str
    name: str | None
    verdict: str | None
    risk_tier: str | None
    trust_score: float | None
    criteria_version: str | None
    model_version: str
    axes: list[AxisEvidenceResponse]


@router.get(
    "/axis-evidence/{server_id}",
    response_model=AxisEvidenceBundleResponse,
)
def get_axis_evidence(
    server_id: str,
    db: Session = Depends(get_session),
) -> AxisEvidenceBundleResponse:
    return get_axis_evidence_bundle(db, server_id)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpServerRegistry

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    axes = [
        ("overall_risk", "high", 6),
        ("auth_strength", "medium", 2),
        ("capability_breadth", "low", 0),
        ("data_sensitivity", "critical", 5),
        ("network_egress", "medium", 3),
        ("maintainer_trust", "low", 1),
        ("exploit_surface", "high", 4),
    ]

    with TestingSession() as db:
        db.add(
            McpServerRegistry(
                server_id="server-test",
                name="Test Server",
                trust_score=0.81,
                verdict="review",
                risk_tier="medium",
                confidence=0.9,
            )
        )
        for index, (axis_name, label, label_index) in enumerate(axes, start=1):
            db.add(
                McpLlmAxisScore(
                    id=index,
                    server_id="server-test",
                    axis_name=axis_name,
                    label=label,
                    label_index=label_index,
                    probs={label: 0.91},
                    p_top=0.91,
                    p_critical=0.03,
                    p_danger=0.06,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                )
            )
        db.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with TestingSession() as db:
            yield db

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/axis-evidence/server-test")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["axes"]) == 7
        assert [axis["label_index"] for axis in payload["axes"]] == sorted(
            axis["label_index"] for axis in payload["axes"]
        )
        assert any(axis["p_top"] == 0.91 for axis in payload["axes"])


if __name__ == "__main__":
    _self_test()
    print("PASS")