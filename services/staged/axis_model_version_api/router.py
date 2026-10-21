from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_model_version_inventory
except ImportError:
    try:
        from .logic import get_axis_model_versions as get_model_version_inventory
    except ImportError:
        from .logic import list_model_versions as get_model_version_inventory


router = APIRouter(prefix="/api", tags=["axes"])


class AxisModelVersion(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: str
    servers: int
    last_scored_at: Optional[datetime]


class AxisModelVersionInventoryResponse(BaseModel):
    versions: List[AxisModelVersion]
    total_versions: int


def _normalize_version(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {
            "model_version": value["model_version"],
            "adapter_sha256": value["adapter_sha256"],
            "decision_rule_version": value["decision_rule_version"],
            "servers": value.get("servers", value.get("server_count", 0)),
            "last_scored_at": value.get("last_scored_at"),
        }

    if hasattr(value, "model_dump"):
        data = value.model_dump()
    elif hasattr(value, "dict"):
        data = value.dict()
    else:
        data = {
            "model_version": value.model_version,
            "adapter_sha256": value.adapter_sha256,
            "decision_rule_version": value.decision_rule_version,
            "servers": getattr(value, "servers", getattr(value, "server_count", 0)),
            "last_scored_at": getattr(value, "last_scored_at", None),
        }

    return {
        "model_version": data["model_version"],
        "adapter_sha256": data["adapter_sha256"],
        "decision_rule_version": data["decision_rule_version"],
        "servers": data.get("servers", data.get("server_count", 0)),
        "last_scored_at": data.get("last_scored_at"),
    }


@router.get(
    "/axes/model-versions",
    response_model=AxisModelVersionInventoryResponse,
    summary="List distinct scored model versions",
)
def list_axis_model_versions(
    session: Session = Depends(get_session),
) -> AxisModelVersionInventoryResponse:
    inventory = get_model_version_inventory(session)

    if isinstance(inventory, dict):
        raw_versions = inventory.get("versions", [])
    elif isinstance(inventory, (list, tuple)):
        raw_versions = inventory
    else:
        raw_versions = []

    versions = [_normalize_version(item) for item in raw_versions]
    return AxisModelVersionInventoryResponse(
        versions=versions,
        total_versions=len(versions),
    )


if __name__ == "__main__":
    from datetime import timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                adapter_sha256 TEXT NOT NULL,
                decision_rule_version TEXT NOT NULL,
                scored_at DATETIME NOT NULL,
                label TEXT,
                label_index INTEGER,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                escalated INTEGER,
                escalated_to TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores (
                server_id, axis_name, model_version, adapter_sha256,
                decision_rule_version, scored_at
            ) VALUES
                ('server-1', 'security', 'model-v1', 'adapter-a', 'rules-v1',
                 '2025-01-01T10:00:00+00:00'),
                ('server-2', 'security', 'model-v1', 'adapter-a', 'rules-v1',
                 '2025-01-02T10:00:00+00:00'),
                ('server-3', 'quality', 'model-v1', 'adapter-a', 'rules-v1',
                 '2025-01-03T10:00:00+00:00'),
                ('server-4', 'security', 'model-v2', 'adapter-b', 'rules-v2',
                 '2025-01-04T10:00:00+00:00')
            """
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/axes/model-versions")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["versions"]) == 2, payload
    assert payload["total_versions"] == 2, payload

    counts = {item["model_version"]: item["servers"] for item in payload["versions"]}
    assert counts == {"model-v1": 3, "model-v2": 1}, payload

    print("PASS")