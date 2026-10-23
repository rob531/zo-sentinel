from __future__ import annotations

from datetime import date, datetime, timezone
from inspect import isawaitable
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_decision_rule_usage


router = APIRouter(prefix="/api", tags=["mcp-llm-axis-decision-rule-usage"])


class DecisionRuleVersionUsage(BaseModel):
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    score_count: int = Field(ge=0)
    distinct_servers: int = Field(ge=0)
    distinct_axes: int = Field(ge=0)
    first_scored_at: str
    last_scored_at: str


class AxisUsage(BaseModel):
    axis_name: str
    count: int = Field(ge=0)


class DecisionRuleUsageResponse(BaseModel):
    total_rows: int = Field(ge=0)
    versions: List[DecisionRuleVersionUsage]
    axes: List[AxisUsage]
    latest: Optional[DecisionRuleVersionUsage] = None
    generated_at: str


def _timestamp(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _version_payload(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict"):
        value = value.dict()
    else:
        value = dict(value)

    return {
        "decision_rule_version": str(value.get("decision_rule_version", "")),
        "model_version": str(value.get("model_version", "")),
        "adapter_sha256": str(value.get("adapter_sha256", "")),
        "score_count": int(value.get("score_count", 0)),
        "distinct_servers": int(
            value.get("distinct_servers", value.get("distinct_server_count", 0))
        ),
        "distinct_axes": int(
            value.get("distinct_axes", value.get("distinct_axis_count", 0))
        ),
        "first_scored_at": _timestamp(value.get("first_scored_at", "")),
        "last_scored_at": _timestamp(value.get("last_scored_at", "")),
    }


def _normalize_response(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict"):
        value = value.dict()
    else:
        value = dict(value)

    versions = [_version_payload(item) for item in value.get("versions", [])]
    axes = []
    for item in value.get("axes", []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif hasattr(item, "dict"):
            item = item.dict()
        else:
            item = dict(item)
        axes.append(
            {
                "axis_name": str(item.get("axis_name", "")),
                "count": int(item.get("count", item.get("row_count", 0))),
            }
        )

    latest_value = value.get("latest", value.get("latest_version_block"))
    latest = _version_payload(latest_value) if latest_value is not None else None

    generated_at = value.get("generated_at") or datetime.now(timezone.utc).isoformat()
    return {
        "total_rows": int(value.get("total_rows", 0)),
        "versions": versions,
        "axes": axes,
        "latest": latest,
        "generated_at": _timestamp(generated_at),
    }


@router.get(
    "/axis-scores/decision-rule-usage",
    response_model=DecisionRuleUsageResponse,
)
async def decision_rule_usage(
    session: Session = Depends(get_session),
) -> DecisionRuleUsageResponse:
    result = get_decision_rule_usage(session)
    if isawaitable(result):
        result = await result
    return DecisionRuleUsageResponse(**_normalize_response(result))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    assert McpLlmAxisScore.__tablename__ == "mcp_llm_axis_scores"

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
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
                    "axis_name": "security",
                    "label": "safe",
                    "label_index": 0,
                    "probs": "{}",
                    "p_top": 0.9,
                    "p_critical": 0.05,
                    "p_danger": 0.05,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "adapter_sha256": "adapter-v1",
                    "scored_at": datetime(2025, 1, 1, 10, 0, 0),
                },
                {
                    "id": 2,
                    "server_id": "server-2",
                    "axis_name": "performance",
                    "label": "safe",
                    "label_index": 0,
                    "probs": "{}",
                    "p_top": 0.8,
                    "p_critical": 0.1,
                    "p_danger": 0.1,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "adapter_sha256": "adapter-v1",
                    "scored_at": datetime(2025, 1, 2, 10, 0, 0),
                },
                {
                    "id": 3,
                    "server_id": "server-3",
                    "axis_name": "security",
                    "label": "safe",
                    "label_index": 0,
                    "probs": "{}",
                    "p_top": 0.7,
                    "p_critical": 0.15,
                    "p_danger": 0.15,
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "rule-v2",
                    "model_version": "model-v2",
                    "adapter_sha256": "adapter-v2",
                    "scored_at": datetime(2025, 1, 3, 10, 0, 0),
                },
            ],
        )

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis-scores/decision-rule-usage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_rows"] == 3
        assert len(payload["versions"]) == 2
        assert {axis["axis_name"]: axis["count"] for axis in payload["axes"]} == {
            "security": 2,
            "performance": 1,
        }
        assert payload["latest"]["decision_rule_version"] == "rule-v2"

    engine.dispose()
    print("PASS")