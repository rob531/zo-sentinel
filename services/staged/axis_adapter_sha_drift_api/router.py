from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_adapter_drift


class AdapterDriftEntry(BaseModel):
    adapter_sha256: str
    row_count: int
    server_count: int
    first_scored_at: Optional[str] = None
    last_scored_at: Optional[str] = None


class ModelVersionEntry(BaseModel):
    model_version: str
    row_count: int


class RuleVersionEntry(BaseModel):
    decision_rule_version: str
    row_count: int


class AdapterDriftResponse(BaseModel):
    total_rows: int
    distinct_adapter_sha256: int
    distinct_model_versions: int
    distinct_decision_rule_versions: int
    adapters: List[AdapterDriftEntry] = []
    model_versions: List[ModelVersionEntry] = []
    rule_versions: List[RuleVersionEntry] = []


router = APIRouter(prefix="/api")


@router.get(
    "/axes/adapter-drift",
    response_model=AdapterDriftResponse,
    summary="Report scoring adapter and model drift",
)
def adapter_drift(
    session: Session = Depends(get_session),
) -> AdapterDriftResponse:
    payload = get_adapter_drift(session)
    if isinstance(payload, AdapterDriftResponse):
        return payload
    return AdapterDriftResponse(**payload)


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    McpLlmAxisScore.__table__.create(test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name="risk",
                    label="high",
                    label_index=0,
                    probs={},
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-a",
                    adapter_sha256="abcdef1234567890",
                    scored_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-a",
                    axis_name="risk",
                    label="medium",
                    label_index=1,
                    probs={},
                    p_top=0.6,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-b",
                    adapter_sha256="abcdef1234567890",
                    scored_at=datetime(2024, 1, 2, 12, 0, 0),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-b",
                    axis_name="risk",
                    label="low",
                    label_index=2,
                    probs={},
                    p_top=0.9,
                    p_critical=0.05,
                    p_danger=0.05,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="rules-v1",
                    model_version="model-a",
                    adapter_sha256="fedcba9876543210",
                    scored_at=datetime(2024, 1, 3, 12, 0, 0),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/axes/adapter-drift")
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 3
    assert data["distinct_adapter_sha256"] == 2
    assert data["distinct_model_versions"] == 2
    assert len(data["adapters"]) == 2
    assert sum(entry["row_count"] for entry in data["adapters"]) == 3
    assert any(entry["server_count"] >= 1 for entry in data["adapters"])
    print("PASS")