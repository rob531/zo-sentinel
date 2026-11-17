from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_tier_movement

router = APIRouter(prefix="/risk/register", tags=["risk-register"])


class ObservationWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_tier: str = Field(alias="from")
    to_tier: str = Field(alias="to")


class TierTransition(BaseModel):
    from_tier: str
    to_tier: str
    count: int


class ServerTierChange(BaseModel):
    server_id: str
    from_tier: str
    to_tier: str


class TierMovementResponse(BaseModel):
    window: ObservationWindow
    transitions: list[TierTransition]
    servers_changed: int
    servers_tracked: int
    by_server: list[ServerTierChange]


@router.get("/tier-movement", response_model=TierMovementResponse)
async def read_tier_movement(
    session: Session = Depends(get_session),
) -> Any:
    result = get_tier_movement(session)
    if inspect.isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    computed_at VARCHAR NOT NULL,
                    server_id VARCHAR NOT NULL,
                    risk_score FLOAT,
                    risk_tier VARCHAR NOT NULL,
                    verdict VARCHAR,
                    reason VARCHAR,
                    meta JSON
                )
                """
            )
        )
        start = datetime(2025, 1, 1, 12, 0, 0)
        rows = [
            ("server-a", "high", 78.0),
            ("server-b", "medium", 52.0),
            ("server-a", "critical", 91.0),
            ("server-b", "low", 29.0),
        ]
        for index, (server_id, tier, score) in enumerate(rows):
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_risk_register (
                        computed_at, server_id, risk_score, risk_tier,
                        verdict, reason, meta
                    ) VALUES (
                        :computed_at, :server_id, :risk_score, :risk_tier,
                        :verdict, :reason, :meta
                    )
                    """
                ),
                {
                    "computed_at": (start + timedelta(minutes=index)).isoformat(),
                    "server_id": server_id,
                    "risk_score": score,
                    "risk_tier": tier,
                    "verdict": tier,
                    "reason": "acceptance fixture",
                    "meta": "{}",
                },
            )

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk/register/tier-movement")
        response.raise_for_status()
        payload = response.json()
        assert payload["servers_tracked"] == 2
        assert payload["servers_changed"] == 2
        assert sum(item["count"] for item in payload["transitions"]) == 2

    print("PASS")