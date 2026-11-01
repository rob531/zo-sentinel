from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_risk_register_trend


class RiskRegisterTrendPoint(BaseModel):
    date: str
    entries: int
    distinct_servers: int


class RiskRegisterTrendEntry(BaseModel):
    server_id: Optional[str]
    computed_at: str
    risk_tier: Optional[str]


class RiskRegisterTrendResponse(BaseModel):
    days: int
    total_entries: int
    series: list[RiskRegisterTrendPoint]
    latest: list[RiskRegisterTrendEntry]


router = APIRouter(prefix="/api", tags=["mcp-risk-register-trend"])


@router.get(
    "/risk/register-trend",
    response_model=RiskRegisterTrendResponse,
)
def risk_register_trend(
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> RiskRegisterTrendResponse:
    result = get_risk_register_trend(session=session, days=days)
    return RiskRegisterTrendResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_session = session_factory()
    test_session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                risk_tier VARCHAR
            )
            """
        )
    )
    test_session.execute(
        text(
            """
            CREATE TABLE mcp_risk_register (
                server_id VARCHAR NOT NULL,
                computed_at TIMESTAMP NOT NULL
            )
            """
        )
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, risk_tier)
            VALUES (:server_id, :risk_tier)
            """
        ),
        [
            {"server_id": "server-a", "risk_tier": "high"},
            {"server_id": "server-b", "risk_tier": "medium"},
        ],
    )
    test_session.execute(
        text(
            """
            INSERT INTO mcp_risk_register (server_id, computed_at)
            VALUES (:server_id, :computed_at)
            """
        ),
        [
            {"server_id": "server-a", "computed_at": now - timedelta(days=2)},
            {"server_id": "server-a", "computed_at": now - timedelta(days=2)},
            {"server_id": "server-b", "computed_at": now - timedelta(days=1)},
        ],
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk/register-trend", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_entries"] == 3
    assert len(payload["series"]) == 2
    assert payload["series"][0]["entries"] == 2
    assert {entry["server_id"] for entry in payload["latest"]} == {
        "server-a",
        "server-b",
    }
    assert {entry["risk_tier"] for entry in payload["latest"]} == {"high", "medium"}

    test_session.close()
    engine.dispose()
    print("PASS")