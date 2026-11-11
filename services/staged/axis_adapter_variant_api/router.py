from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

from .logic import get_adapter_variant_summary

router = APIRouter(prefix="/api", tags=["axis-adapter-variant"])


class AdapterVariantBucket(BaseModel):
    adapter_sha256: str
    model_version: str
    rows: int
    distinct_servers: int
    first_scored_at: Optional[datetime] = None
    last_scored_at: Optional[datetime] = None


class AxisAdapterVariantResponse(BaseModel):
    adapters: List[AdapterVariantBucket]
    distinct_adapters: int
    distinct_models: int
    total_rows: int


@router.get(
    "/axes/adapter-variant",
    response_model=AxisAdapterVariantResponse,
)
def get_axis_adapter_variant(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> AxisAdapterVariantResponse:
    return get_adapter_variant_summary(session, days)


def _self_test() -> None:
    from fastapi import FastAPI

    axis_name = None
    for value in get_adapter_variant_summary.__globals__.values():
        if isinstance(value, (list, tuple, set, frozenset)) and len(value) == 7:
            if all(isinstance(item, str) for item in value):
                axis_name = sorted(value)[0]
                break
    if axis_name is None:
        axis_name = "security"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT NOT NULL,
                    adapter_sha256 TEXT,
                    scored_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id, server_id, axis_name, label, label_index, probs,
                    p_top, p_critical, p_danger, escalated, escalated_to,
                    decision_rule_version, model_version, adapter_sha256, scored_at
                ) VALUES (
                    :id, :server_id, :axis_name, :label, :label_index, :probs,
                    :p_top, :p_critical, :p_danger, :escalated, :escalated_to,
                    :decision_rule_version, :model_version, :adapter_sha256, :scored_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-1",
                    "axis_name": axis_name,
                    "label": "safe",
                    "label_index": 0,
                    "probs": '{"safe":1.0}',
                    "p_top": 1.0,
                    "p_critical": 0.0,
                    "p_danger": 0.0,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                    "adapter_sha256": "adapter-1",
                    "scored_at": datetime.now(timezone.utc),
                },
                {
                    "id": 2,
                    "server_id": "server-1",
                    "axis_name": axis_name,
                    "label": "safe",
                    "label_index": 0,
                    "probs": '{"safe":1.0}',
                    "p_top": 1.0,
                    "p_critical": 0.0,
                    "p_danger": 0.0,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                    "adapter_sha256": "adapter-1",
                    "scored_at": datetime.now(timezone.utc),
                },
                {
                    "id": 3,
                    "server_id": "server-2",
                    "axis_name": axis_name,
                    "label": "safe",
                    "label_index": 0,
                    "probs": '{"safe":1.0}',
                    "p_top": 1.0,
                    "p_critical": 0.0,
                    "p_danger": 0.0,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "v1",
                    "model_version": "model-1",
                    "adapter_sha256": "adapter-1",
                    "scored_at": datetime.now(timezone.utc),
                },
                {
                    "id": 4,
                    "server_id": "server-3",
                    "axis_name": axis_name,
                    "label": "safe",
                    "label_index": 0,
                    "probs": '{"safe":1.0}',
                    "p_top": 1.0,
                    "p_critical": 0.0,
                    "p_danger": 0.0,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "v1",
                    "model_version": "model-2",
                    "adapter_sha256": "adapter-2",
                    "scored_at": datetime.now(timezone.utc),
                },
            ],
        )

    testing_app = FastAPI()
    testing_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    testing_app.dependency_overrides[get_session] = override_get_session

    with TestClient(testing_app) as client:
        response = client.get(
            "/api/axes/adapter-variant",
            params={"days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["distinct_adapters"] == 2, payload
        assert payload["distinct_models"] == 2, payload
        assert payload["total_rows"] == 4, payload
        assert any(bucket["distinct_servers"] == 2 for bucket in payload["adapters"]), payload

    print("PASS")


if __name__ == "__main__":
    _self_test()