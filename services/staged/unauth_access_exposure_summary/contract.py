from __future__ import annotations

from collections.abc import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from services.staged.unauth_access_exposure_summary.logic import (
    unauth_exposure_summary,
)
from services.staged.unauth_access_exposure_summary.router import router


app = FastAPI()
app.include_router(router)


def build_test_session() -> tuple[Session, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_factory()

    session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE mcp_unauth_access (
                server_id TEXT,
                url TEXT,
                unauth_access TEXT,
                source TEXT,
                updated_at TEXT
            )
            """
        )
    )

    session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES (:server_id, :name, :risk_tier)
            """
        ),
        [
            {"server_id": "server-alpha", "name": "Alpha Server", "risk_tier": "high"},
            {"server_id": "server-beta", "name": "Beta Server", "risk_tier": "medium"},
            {"server_id": "server-gamma", "name": "Gamma Server", "risk_tier": "low"},
        ],
    )
    session.execute(
        text(
            """
            INSERT INTO mcp_unauth_access
                (server_id, url, unauth_access, source, updated_at)
            VALUES
                (:server_id, :url, :unauth_access, :source, :updated_at)
            """
        ),
        [
            {
                "server_id": "server-alpha",
                "url": "https://alpha.example.test/mcp",
                "unauth_access": "enabled",
                "source": "registry_scan",
                "updated_at": "2025-01-03T12:00:00Z",
            },
            {
                "server_id": "server-beta",
                "url": "https://beta.example.test/mcp",
                "unauth_access": "read-only",
                "source": "registry_scan",
                "updated_at": "2025-01-02T12:00:00Z",
            },
            {
                "server_id": "server-gamma",
                "url": "https://gamma.example.test/mcp",
                "unauth_access": "none",
                "source": "manual_review",
                "updated_at": "2025-01-01T12:00:00Z",
            },
        ],
    )
    session.commit()
    return session, session_factory


def run() -> None:
    session, session_factory = build_test_session()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session

    try:
        direct_result = unauth_exposure_summary(session)
        assert direct_result["exposed_servers"] == 2
        assert direct_result["total_servers"] == 3
        assert len(direct_result["top_exposed"]) == 2

        with TestClient(app) as client:
            response = client.get("/api/security/unauth-summary")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["exposed_servers"] == 2
            assert payload["total_servers"] == 3
            assert len(payload["top_exposed"]) == 2
            assert isinstance(payload["exposure_pct"], float)
            assert payload["by_source"]["registry_scan"] == 2
            assert payload["by_risk_tier"]["high"] == 1
            assert payload["by_risk_tier"]["medium"] == 1
            assert {
                row["server_id"] for row in payload["top_exposed"]
            } == {"server-alpha", "server-beta"}
    finally:
        app.dependency_overrides.clear()
        session.close()
        session_factory.kw["bind"].dispose()

    print("PASS")


if __name__ == "__main__":
    run()