"""HTTP surface for decision-rule version rollups."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import decision_rule_version_rollup

router = APIRouter(prefix="/api", tags=["scoring"])


class DecisionRuleVersionRollupItem(BaseModel):
    decision_rule_version: str | None
    model_version: str
    rows: int
    servers: int
    avg_p_top: float | None
    max_p_top: float | None
    latest_scored_at: datetime | None


class DecisionRuleVersionRollupResponse(BaseModel):
    versions: list[DecisionRuleVersionRollupItem]
    total_versions: int


@router.get(
    "/scoring/decision-rule-rollup",
    response_model=DecisionRuleVersionRollupResponse,
)
def get_decision_rule_version_rollup(
    db: Session = Depends(get_session),
) -> DecisionRuleVersionRollupResponse:
    result = decision_rule_version_rollup(db)
    versions = result.get("versions", []) if isinstance(result, dict) else result
    return DecisionRuleVersionRollupResponse(
        versions=versions,
        total_versions=len(versions),
    )


def _self_test() -> None:
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
    McpLlmAxisScore.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    axis_name="top",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    server_id="server-1",
                    p_top=0.4,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=2,
                    axis_name="top",
                    decision_rule_version="rules-v1",
                    model_version="model-v1",
                    server_id="server-2",
                    p_top=0.6,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=3,
                    axis_name="top",
                    decision_rule_version="rules-v2",
                    model_version="model-v1",
                    server_id="server-3",
                    p_top=0.9,
                    scored_at=now,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scoring/decision-rule-rollup")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["versions"]) == 2, payload
        assert payload["total_versions"] == 2, payload

        by_version = {
            item["decision_rule_version"]: item for item in payload["versions"]
        }
        assert by_version["rules-v1"]["rows"] == 2, payload
        assert by_version["rules-v1"]["servers"] == 2, payload
        assert abs(by_version["rules-v1"]["avg_p_top"] - 0.5) < 1e-9, payload
        assert by_version["rules-v2"]["rows"] == 1, payload
        assert abs(by_version["rules-v2"]["avg_p_top"] - 0.9) < 1e-9, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()