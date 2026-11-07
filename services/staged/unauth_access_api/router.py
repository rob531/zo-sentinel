"""HTTP surface for the unauthenticated MCP access API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess

from .logic import get_unauth_access as _get_unauth_access

router = APIRouter(prefix="/api", tags=["unauth-access"])


class UnauthAccessRow(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    url: str | None = None
    unauth_access: str | None = None
    source: str | None = None
    updated_at: datetime | None = None


class UnauthAccessSummary(BaseModel):
    total: int
    by_unauth_access: dict[str, int]
    by_source: dict[str, int]
    by_risk_tier: dict[str, int]


class UnauthAccessResponse(BaseModel):
    rows: list[UnauthAccessRow]
    summary: UnauthAccessSummary
    generated_at: datetime


@router.get("/servers/unauth-access", response_model=UnauthAccessResponse)
def get_unauth_access(
    risk_tier: str | None = Query(default=None),
    unauth_access: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> UnauthAccessResponse:
    result: dict[str, Any] = _get_unauth_access(
        db,
        risk_tier=risk_tier,
        unauth_access=unauth_access,
        limit=limit,
    )
    return UnauthAccessResponse(**result)


if __name__ == "__main__":
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
    McpUnauthAccess.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Alpha",
                    risk_tier="high",
                    trust_score=0.25,
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Beta",
                    risk_tier="low",
                    trust_score=0.90,
                ),
                McpServerRegistry(
                    server_id="srv-3",
                    name="Gamma",
                    risk_tier="medium",
                    trust_score=0.60,
                ),
            ]
        )
        session.add_all(
            [
                McpUnauthAccess(
                    server_id="srv-1",
                    url="https://alpha.example/mcp",
                    unauth_access="enabled",
                    source="scanner",
                ),
                McpUnauthAccess(
                    server_id="srv-2",
                    url="https://beta.example/mcp",
                    unauth_access="disabled",
                    source="scanner",
                ),
                McpUnauthAccess(
                    server_id="srv-3",
                    url="https://gamma.example/mcp",
                    unauth_access="enabled",
                    source="registry",
                ),
                McpUnauthAccess(
                    server_id="srv-4",
                    url="https://delta.example/mcp",
                    unauth_access="disabled",
                    source="registry",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/servers/unauth-access")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["summary"]["total"] == 4, payload
        assert len(payload["summary"]["by_unauth_access"]) == 2, payload
        assert len(payload["summary"]["by_source"]) == 2, payload

        filtered = client.get(
            "/api/servers/unauth-access",
            params={"risk_tier": "high"},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["summary"]["total"] == 1, filtered_payload
        assert all(
            row["risk_tier"] == "high" for row in filtered_payload["rows"]
        ), filtered_payload

    print("PASS")