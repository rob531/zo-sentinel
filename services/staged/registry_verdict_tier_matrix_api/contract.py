from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from . import logic as registry_logic


def _normalise_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "dict"):
        return result.dict()
    return result


def _create_test_app() -> FastAPI:
    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-tier-matrix")
    def verdict_tier_matrix(session: Session = Depends(get_session)) -> Any:
        return _normalise_result(registry_logic.get_verdict_tier_matrix(session))

    return test_app


def _seed_test_data(session: Session) -> None:
    assessed_at = datetime.now(timezone.utc)
    servers = [
        McpServerRegistry(
            server_id="registry-matrix-1",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            registry_source="source-a",
            last_assessed=assessed_at,
        ),
        McpServerRegistry(
            server_id="registry-matrix-2",
            verdict="TRUSTED_GENERAL",
            risk_tier="MEDIUM",
            registry_source="source-a",
            last_assessed=assessed_at,
        ),
        McpServerRegistry(
            server_id="registry-matrix-3",
            verdict="TRUSTED_GENERAL",
            risk_tier="HIGH",
            registry_source="source-b",
            last_assessed=assessed_at,
        ),
        McpServerRegistry(
            server_id="registry-matrix-4",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="LOW",
            registry_source="source-b",
            last_assessed=assessed_at,
        ),
        McpServerRegistry(
            server_id="registry-matrix-5",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="MEDIUM",
            registry_source="source-b",
            last_assessed=assessed_at,
        ),
        McpServerRegistry(
            server_id="registry-matrix-6",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="HIGH",
            registry_source="source-a",
            last_assessed=assessed_at,
        ),
    ]
    session.add_all(servers)
    session.commit()


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = _create_test_app()
    test_app.dependency_overrides[get_session] = override_get_session

    with session_factory() as session:
        _seed_test_data(session)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-tier-matrix")
        assert response.status_code == 200, response.text
        payload = response.json()

    assert payload["total_servers"] == 6
    assert sum(item["count"] for item in payload["matrix"]) == payload["total_servers"]
    assert len(payload["by_registry_source"]) == 2
    assert sum(item["count"] for item in payload["by_registry_source"]) == 6
    assert set(payload["by_verdict"]) == {"TRUSTED_GENERAL", "HIGH_RISK_ISOLATED"}
    assert set(payload["by_risk_tier"]) == {"LOW", "MEDIUM", "HIGH"}

    print("PASS")


if __name__ == "__main__":
    main()