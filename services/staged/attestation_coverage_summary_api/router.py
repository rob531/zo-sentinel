from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

if __package__:
    from .logic import get_attestation_coverage_summary
else:
    from logic import get_attestation_coverage_summary


router = APIRouter(prefix="/api")


@router.get("/attestations/coverage")
def attestation_coverage_summary(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_attestation_coverage_summary(session)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    scored_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )

    now = datetime.utcnow()
    stale_at = now - timedelta(days=31)

    with test_session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
                """
            ),
            [
                {"server_id": "attested-server", "name": "Attested Server"},
                {"server_id": "unattested-server", "name": "Unattested Server"},
                {"server_id": "stale-server", "name": "Stale Server"},
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, scored_at)
                VALUES (:server_id, :axis_name, :scored_at)
                """
            ),
            [
                {
                    "server_id": "attested-server",
                    "axis_name": "security",
                    "scored_at": now,
                },
                {
                    "server_id": "unattested-server",
                    "axis_name": "security",
                    "scored_at": now,
                },
                {
                    "server_id": "stale-server",
                    "axis_name": "security",
                    "scored_at": now,
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_attestations (server_id, created_at)
                VALUES (:server_id, :created_at)
                """
            ),
            [
                {"server_id": "attested-server", "created_at": now},
                {"server_id": "stale-server", "created_at": stale_at},
            ],
        )
        session.commit()

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/attestations/coverage")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 3, payload
    assert payload["attested_servers"] == 2, payload
    assert payload["coverage_pct"] == 66.67, payload
    assert payload["stale_attestations"] == 1, payload
    assert any(
        item["server_id"] == "unattested-server"
        for item in payload["unattested_sample"]
    ), payload
    assert isinstance(payload["generated_at"], str), payload
    datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()