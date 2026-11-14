"""HTTP surface for adapter SHA-256 reuse auditing."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import adapter_sha256_reuse_audit

router = APIRouter(prefix="/api", tags=["scoring"])


class AdapterReuseGroupResponse(BaseModel):
    adapter_sha256: str
    model_version: str | None = None
    server_count: int
    server_ids: list[str] = []
    axis_rows: int


class AdapterReuseAuditResponse(BaseModel):
    as_of: datetime
    groups: list[AdapterReuseGroupResponse] = []
    shared_count: int
    unscored_adapter_rows: int
    total_groups: int


@router.get("/scoring/adapter-reuse", response_model=AdapterReuseAuditResponse)
def get_adapter_reuse_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> AdapterReuseAuditResponse:
    result: dict[str, Any] = adapter_sha256_reuse_audit(db, limit=limit)
    return AdapterReuseAuditResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    adapter_sha256="shared-sha",
                    model_version="model-1",
                    server_id="server-a",
                    axis_name="security",
                    p_top=0.91,
                ),
                McpLlmAxisScore(
                    id=2,
                    adapter_sha256="shared-sha",
                    model_version="model-1",
                    server_id="server-b",
                    axis_name="security",
                    p_top=0.89,
                ),
                McpLlmAxisScore(
                    id=3,
                    adapter_sha256="unique-sha",
                    model_version="model-1",
                    server_id="server-c",
                    axis_name="security",
                    p_top=0.88,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/adapter-reuse", params={"limit": 100})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["shared_count"] == 1, payload
        shared = [group for group in payload["groups"] if group["server_count"] > 1]
        assert len(shared) == 1, payload
        assert shared[0]["adapter_sha256"] == "shared-sha", payload
        assert shared[0]["server_count"] == 2, payload
        assert "unique-sha" not in {
            group["adapter_sha256"] for group in payload["groups"]
        }, payload

    print("PASS")