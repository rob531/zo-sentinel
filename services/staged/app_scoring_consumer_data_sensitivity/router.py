from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import get_data_sensitivity_score as _logic

router = APIRouter(prefix="/api")


class DataSensitivityResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    axis: str = "data_sensitivity"
    scored: bool
    id: Optional[int] = None
    label: Optional[str] = None
    label_index: Optional[int] = None
    probs: Optional[Any] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    escalated_to: Optional[str] = None
    decision_rule_version: Optional[str] = None
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None
    scored_at: Optional[datetime] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    confidence: Optional[float] = None
    last_assessed: Optional[datetime] = None


def _invoke_logic(server_id: str, session: Any) -> Any:
    signature = inspect.signature(_logic)
    arguments: list[Any] = []
    keywords: dict[str, Any] = {}

    for parameter in signature.parameters.values():
        if parameter.name == "server_id":
            value = server_id
        elif parameter.name in {"session", "db"}:
            value = session
        elif parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        else:
            continue

        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            arguments.append(value)
        else:
            keywords[parameter.name] = value

    if arguments and not keywords:
        return _logic(*arguments)
    if keywords:
        return _logic(**keywords)
    return _logic(server_id, session)


@router.get(
    "/scoring/consumers/data-sensitivity",
    response_model=DataSensitivityResponse,
    response_model_exclude_none=False,
)
def get_data_sensitivity(
    server_id: str,
    session: Any = Depends(get_session),
) -> Any:
    result = _invoke_logic(server_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")

    if not isinstance(result, dict):
        result = {
            field: getattr(result, field)
            for field in DataSensitivityResponse.model_fields
            if hasattr(result, field)
        }

    payload = dict(result)
    payload.setdefault("server_id", server_id)
    payload.setdefault("axis", "data_sensitivity")
    payload.setdefault("scored", payload.get("label") is not None)
    return payload


get_consumer_data_sensitivity = get_data_sensitivity
get_scoring_consumer_data_sensitivity = get_data_sensitivity


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    store = {
        "scored-server": {
            "server_id": "scored-server",
            "name": "Scored Server",
            "axis": "data_sensitivity",
            "scored": True,
            "id": 1,
            "label": "ELEVATED",
            "label_index": 2,
            "probs": {"top": 0.61, "critical": 0.12, "danger": 0.27},
            "p_top": 0.61,
            "p_critical": 0.12,
            "p_danger": 0.27,
            "escalated": False,
            "escalated_to": None,
            "decision_rule_version": "v1",
            "model_version": "sft-student",
            "adapter_sha256": "abc123",
            "scored_at": "2025-01-01T00:00:00Z",
            "verdict": "REVIEW",
            "risk_tier": "medium",
            "trust_score": 0.75,
            "confidence": 0.90,
            "last_assessed": "2025-01-01T00:00:00Z",
        },
        "unscored-server": {
            "server_id": "unscored-server",
            "name": "Unscored Server",
            "axis": "data_sensitivity",
            "scored": False,
            "id": None,
            "label": None,
            "label_index": None,
            "probs": None,
            "p_top": None,
            "p_critical": None,
            "p_danger": None,
            "escalated": None,
            "escalated_to": None,
            "decision_rule_version": None,
            "model_version": None,
            "adapter_sha256": None,
            "scored_at": None,
            "verdict": "UNKNOWN",
            "risk_tier": None,
            "trust_score": None,
            "confidence": None,
            "last_assessed": None,
        },
    }

    def override_get_session() -> Any:
        yield object()

    def test_logic(server_id: str, session: Any) -> Any:
        if server_id not in store:
            raise HTTPException(status_code=404, detail="Server not found")
        return store[server_id]

    test_app.dependency_overrides[get_session] = override_get_session
    original_logic = _logic
    globals()["_logic"] = test_logic

    try:
        with TestClient(test_app) as client:
            scored = client.get(
                "/api/scoring/consumers/data-sensitivity",
                params={"server_id": "scored-server"},
            )
            assert scored.status_code == 200
            assert scored.json()["scored"] is True
            assert scored.json()["label"] == "ELEVATED"

            unscored = client.get(
                "/api/scoring/consumers/data-sensitivity",
                params={"server_id": "unscored-server"},
            )
            assert unscored.status_code == 200
            assert unscored.json()["scored"] is False
            assert unscored.json()["label"] is None

            unknown = client.get(
                "/api/scoring/consumers/data-sensitivity",
                params={"server_id": "unknown-server"},
            )
            assert unknown.status_code == 404
    finally:
        globals()["_logic"] = original_logic
        test_app.dependency_overrides.clear()

    print("PASS")