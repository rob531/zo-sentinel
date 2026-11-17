from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_duplicate_url_groups


class DuplicateServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_seen: Optional[datetime] = None


class DuplicateUrlGroup(BaseModel):
    url: str
    count: int
    servers: list[DuplicateServer]


class DuplicateUrlResponse(BaseModel):
    generated_at: datetime
    total_groups: int
    duplicate_servers: int
    groups: list[DuplicateUrlGroup]


router = APIRouter(prefix="/api")


@router.get("/registry/duplicate-urls", response_model=DuplicateUrlResponse)
def get_duplicate_urls(
    session: Session = Depends(get_session),
) -> DuplicateUrlResponse:
    return DuplicateUrlResponse.model_validate(get_duplicate_url_groups(session))


if __name__ == "__main__":
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

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    url VARCHAR,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    last_seen TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, url, verdict, risk_tier, last_seen)
                VALUES
                    ('one', 'Server One', 'source-a', 'HTTPS://Example.com/', 'malicious', 'high', '2025-01-01 00:00:00'),
                    ('two', 'Server Two', 'source-b', 'https://example.com', 'suspicious', 'medium', '2025-01-02 00:00:00'),
                    ('three', 'Server Three', 'source-c', 'https://unique.example/path', 'trusted', 'low', '2025-01-03 00:00:00')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)
    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/duplicate-urls")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_groups"] == 1
    assert payload["groups"][0]["count"] == 2
    assert payload["groups"][0]["url"] == "https://example.com"
    print("PASS")