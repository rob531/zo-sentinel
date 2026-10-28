from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink
from .logic import get_server_coverage

router = APIRouter(prefix="/api")


class CoveredServer(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    link_count: int


class ServerCoverageResponse(BaseModel):
    total_servers: int
    covered_servers: int
    coverage_pct: float
    top: List[CoveredServer]


@router.get(
    "/vuln/servers/covered",
    response_model=ServerCoverageResponse,
    summary="Report advisory-link coverage across registered MCP servers",
)
async def server_coverage(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ServerCoverageResponse:
    result = get_server_coverage(session, limit)
    if inspect.isawaitable(result):
        result = await result
    return ServerCoverageResponse.model_validate(result)


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-covered",
                    name="Covered Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-uncovered-1",
                    name="Uncovered Server One",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-uncovered-2",
                    name="Uncovered Server Two",
                    risk_tier="low",
                ),
            ]
        )
        linked_at = datetime.now(timezone.utc)
        session.add_all(
            [
                VulnLink(
                    advisory_id="ADV-001",
                    server_id="server-covered",
                    match_basis="hostname",
                    match_value="covered.example",
                    match_confidence=95.0,
                    linked_at=linked_at,
                ),
                VulnLink(
                    advisory_id="ADV-002",
                    server_id="server-covered",
                    match_basis="certificate",
                    match_value="covered.example",
                    match_confidence=90.0,
                    linked_at=linked_at,
                ),
            ]
        )
        session.commit()

        from fastapi import FastAPI

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            with factory() as db_session:
                yield db_session

        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get("/api/vuln/servers/covered", params={"limit": 5})
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_servers"] == 3
            assert payload["covered_servers"] == 1
            assert 30 <= payload["coverage_pct"] <= 34
            assert payload["top"][0]["link_count"] == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()