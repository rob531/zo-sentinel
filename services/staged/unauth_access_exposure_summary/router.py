"""HTTP surface for the unauthenticated-access exposure summary."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess

from .logic import unauth_exposure_summary


router = APIRouter(prefix="/api/security", tags=["security"])


class ExposedServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    url: str | None = None
    unauth_access: str | None = None
    source: str | None = None
    updated_at: datetime | None = None


class UnauthExposureSummaryResponse(BaseModel):
    exposed_servers: int
    total_servers: int
    exposure_pct: float
    by_source: dict[str, int]
    by_risk_tier: dict[str, int]
    top_exposed: list[ExposedServer]


@router.get(
    "/unauth-summary",
    response_model=UnauthExposureSummaryResponse,
)
def get_unauth_exposure_summary(
    db: Session = Depends(get_session),
) -> UnauthExposureSummaryResponse:
    summary = unauth_exposure_summary(db)
    return UnauthExposureSummaryResponse.model_validate(summary)


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
    McpServerRegistry.__table__.create(bind=test_engine)
    McpUnauthAccess.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="srv-exposed-1",
                    name="Exposed One",
                    risk_tier="high",
                    url="https://one.example",
                ),
                McpServerRegistry(
                    server_id="srv-exposed-2",
                    name="Exposed Two",
                    risk_tier="medium",
                    url="https://two.example",
                ),
                McpServerRegistry(
                    server_id="srv-protected",
                    name="Protected",
                    risk_tier="low",
                    url="https://protected.example",
                ),
                McpUnauthAccess(
                    server_id="srv-exposed-1",
                    url="https://one.example",
                    unauth_access="enabled",
                    source="scan",
                ),
                McpUnauthAccess(
                    server_id="srv-exposed-2",
                    url="https://two.example",
                    unauth_access="public",
                    source="scan",
                ),
                McpUnauthAccess(
                    server_id="srv-protected",
                    url="https://protected.example",
                    unauth_access="none",
                    source="scan",
                ),
            ]
        )
        db.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: db

        response = TestClient(test_app).get("/api/security/unauth-summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["exposed_servers"] == 2
        assert payload["total_servers"] == 3
        assert len(payload["top_exposed"]) == 2

    print("PASS")