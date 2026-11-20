"""HTTP surface for verdict-ageing buckets.

Exposes the staged service router and delegates bucket calculation to the
service-local logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import verdict_ageing_buckets

router = APIRouter(prefix="/api", tags=["verdict-ageing"])


class VerdictAgeingResponse(BaseModel):
    total: int
    buckets: dict[str, int]


@router.get("/registry/verdict-ageing", response_model=VerdictAgeingResponse)
def get_verdict_ageing(db: Session = Depends(get_session)) -> VerdictAgeingResponse:
    buckets = verdict_ageing_buckets(db)
    return VerdictAgeingResponse(total=sum(buckets.values()), buckets=buckets)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    assert McpServerRegistry.__tablename__ == "mcp_server_registry"

    original_buckets = verdict_ageing_buckets
    verdict_ageing_buckets = lambda _db: {"0-7 days": 2, "8-30 days": 1}

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: None

    try:
        response = TestClient(that_app).get("/api/registry/verdict-ageing")
        assert response.status_code == 200
        assert response.json() == {
            "total": 3,
            "buckets": {"0-7 days": 2, "8-30 days": 1},
        }
    finally:
        verdict_ageing_buckets = original_buckets

    print("PASS")