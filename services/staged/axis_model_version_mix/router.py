"""HTTP router for model-version mix reporting."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_model_version_mix as _model_version_mix
except ImportError:
    from .logic import model_version_mix as _model_version_mix

router = APIRouter(prefix="/api", tags=["axis-model-version-mix"])


class ModelVersionSummary(BaseModel):
    model_version: str
    servers: int
    rows: int
    escalated: int
    newest_scored_at: str


class ModelVersionMixResponse(BaseModel):
    model_versions: list[ModelVersionSummary]
    axis_rows: dict[str, int]
    adapter_sha256: list[str]
    decision_rule_versions: list[str]


@router.get("/axes/model-version-mix", response_model=ModelVersionMixResponse)
def get_model_version_mix(
    db: Session = Depends(get_session),
) -> ModelVersionMixResponse:
    return ModelVersionMixResponse(**_model_version_mix(db))


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc)
    rows = [
        McpLlmAxisScore(
            id=1,
            server_id="server-1",
            axis_name="overall_risk",
            model_version="model-a",
            adapter_sha256="adapter-a",
            decision_rule_version="rule-1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            server_id="server-2",
            axis_name="auth_strength",
            model_version="model-a",
            adapter_sha256="adapter-a",
            decision_rule_version="rule-1",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3,
            server_id="server-2",
            axis_name="overall_risk",
            model_version="model-b",
            adapter_sha256="adapter-b",
            decision_rule_version="rule-2",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=4,
            server_id="server-3",
            axis_name="capability_breadth",
            model_version="model-b",
            adapter_sha256="adapter-b",
            decision_rule_version="rule-2",
            scored_at=now,
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    with TestClient(test_app) as client:
        response = client.get("/api/axes/model-version-mix")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["model_versions"]) == 2
    assert next(
        item["servers"]
        for item in payload["model_versions"]
        if item["model_version"] == "model-a"
    ) == 2
    assert set(payload["axis_rows"]).issubset(
        {
            "overall_risk",
            "auth_strength",
            "capability_breadth",
            "data_sensitivity",
            "network_egress",
            "maintainer_trust",
            "exploit_surface",
        }
    )

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()