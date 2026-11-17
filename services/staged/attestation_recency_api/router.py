from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_attestation_recency as fetch_attestation_recency


router = APIRouter(prefix="/api", tags=["attestation-recency"])


class AttestationBand(BaseModel):
    band: str
    count: int


class StaleServer(BaseModel):
    server_id: str
    last_attested_at: datetime | None = None
    age_days: float


class AttestationRecencyResponse(BaseModel):
    days: int
    total: int
    bands: List[AttestationBand]
    stale_servers: List[StaleServer]
    servers_without_attestation: int


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {
        "days": getattr(value, "days", 0),
        "total": getattr(value, "total", 0),
        "bands": getattr(value, "bands", []),
        "stale_servers": getattr(value, "stale_servers", []),
        "servers_without_attestation": getattr(
            value, "servers_without_attestation", 0
        ),
    }


@router.get(
    "/attestations/recency",
    response_model=AttestationRecencyResponse,
    summary="Get attestation recency by server",
)
def attestation_recency(
    days: int = Query(30, ge=0),
    session: Session = Depends(get_session),
) -> AttestationRecencyResponse:
    result = fetch_attestation_recency(session, days)
    return AttestationRecencyResponse(**_as_dict(result))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    paths = {route.path for route in test_app.routes}
    assert "/api/attestations/recency" in paths
    assert McpServerRegistry is not None
    print("PASS")