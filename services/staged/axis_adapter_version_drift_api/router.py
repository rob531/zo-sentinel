from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_adapter_version_drift


class AdapterVersionDriftEntry(BaseModel):
    adapter_sha256: str
    rows: int
    servers: int
    model_versions: List[str]
    latest_scored_at: Optional[datetime]
    drifted: bool


class AdapterVersionDriftResponse(BaseModel):
    days: int
    adapters: List[AdapterVersionDriftEntry]


router = APIRouter(prefix="/api")


@router.get(
    "/scoring/adapter-version-drift",
    response_model=AdapterVersionDriftResponse,
)
def adapter_version_drift(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> AdapterVersionDriftResponse:
    return get_adapter_version_drift(session, days)


if __name__ == "__main__":
    original_get_adapter_version_drift = get_adapter_version_drift

    def fake_get_adapter_version_drift(session, days):
        return {
            "days": days,
            "adapters": [
                {
                    "adapter_sha256": "adapter-drifted",
                    "rows": 2,
                    "servers": 2,
                    "model_versions": ["model-a", "model-b"],
                    "latest_scored_at": "2025-01-02T03:04:05Z",
                    "drifted": True,
                },
                {
                    "adapter_sha256": "adapter-stable",
                    "rows": 2,
                    "servers": 2,
                    "model_versions": ["model-a"],
                    "latest_scored_at": "2025-01-02T03:04:05Z",
                    "drifted": False,
                },
            ],
        }

    try:
        globals()["get_adapter_version_drift"] = fake_get_adapter_version_drift

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = lambda: None

        response = TestClient(that_app).get(
            "/api/scoring/adapter-version-drift",
            params={"days": 30},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["days"] == 30
        assert len(payload["adapters"]) == 2
        assert payload["adapters"][0]["drifted"] is True
        assert payload["adapters"][1]["drifted"] is False
        assert all(adapter["rows"] >= 1 for adapter in payload["adapters"])
        assert all(adapter["servers"] >= 1 for adapter in payload["adapters"])
    finally:
        globals()["get_adapter_version_drift"] = original_get_adapter_version_drift

    print("PASS")