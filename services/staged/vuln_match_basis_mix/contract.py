from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink

from .logic import compute_match_basis_mix
from .router import router


def _include_router(application: FastAPI) -> None:
    paths = [getattr(route, "path", "") for route in router.routes]
    if any(path.startswith("/api/vuln/match-basis") for path in paths):
        application.include_router(router)
    else:
        application.include_router(router, prefix="/api")


def run_self_test() -> None:
    assert VulnLink.__tablename__ == "vuln_links"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER NOT NULL,
                    server_id INTEGER NOT NULL,
                    match_basis TEXT,
                    match_value TEXT NOT NULL,
                    match_confidence REAL NOT NULL,
                    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id, advisory_id, server_id, match_basis,
                    match_value, match_confidence, linked_at
                ) VALUES (
                    :id, :advisory_id, :server_id, :match_basis,
                    :match_value, :match_confidence, :linked_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "advisory_id": 101,
                    "server_id": 201,
                    "match_basis": "package",
                    "match_value": "requests",
                    "match_confidence": 0.9,
                    "linked_at": "2025-01-01T00:00:00",
                },
                {
                    "id": 2,
                    "advisory_id": 102,
                    "server_id": 202,
                    "match_basis": "package",
                    "match_value": "urllib3",
                    "match_confidence": 0.2,
                    "linked_at": "2025-01-01T00:00:00",
                },
                {
                    "id": 3,
                    "advisory_id": 103,
                    "server_id": 203,
                    "match_basis": "identity",
                    "match_value": "github:example/server",
                    "match_confidence": 0.6,
                    "linked_at": "2025-01-01T00:00:00",
                },
                {
                    "id": 4,
                    "advisory_id": 104,
                    "server_id": 204,
                    "match_basis": None,
                    "match_value": "unmatched",
                    "match_confidence": 0.8,
                    "linked_at": "2025-01-01T00:00:00",
                },
            ],
        )

    session: Session = session_factory()

    def override_get_session() -> Iterator[Session]:
        yield session

    that_app = FastAPI()
    _include_router(that_app)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/vuln/match-basis")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 4
            assert payload["low_confidence_total"] == 1

            by_basis = {item["basis"]: item for item in payload["by_basis"]}
            assert by_basis["package"]["count"] == 2
            assert by_basis["package"]["avg_confidence"] == 0.55
            assert by_basis["package"]["low_confidence_count"] == 1
            assert by_basis["identity"]["count"] == 1
            assert by_basis["identity"]["avg_confidence"] == 0.6
            assert by_basis["identity"]["low_confidence_count"] == 0
            assert by_basis["unknown"]["count"] == 1
            assert by_basis["unknown"]["avg_confidence"] == 0.8
            assert by_basis["unknown"]["low_confidence_count"] == 0

        computed = compute_match_basis_mix(
            [
                {
                    "match_basis": "package",
                    "match_confidence": 0.9,
                },
                {
                    "match_basis": "package",
                    "match_confidence": 0.2,
                },
                {
                    "match_basis": "identity",
                    "match_confidence": 0.6,
                },
                {
                    "match_basis": None,
                    "match_confidence": 0.8,
                },
            ]
        )
        assert computed["total"] == 4
        assert computed["low_confidence_total"] == 1
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()