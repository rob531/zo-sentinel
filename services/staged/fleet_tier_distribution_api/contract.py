"""Acceptance self-test for the fleet tier distribution service."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .router import router


ALLOWED_TIERS = frozenset(
    {
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    }
)


def run() -> bool:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    try:
        with test_session_factory() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="contract-tier-trusted",
                        name="Contract Trusted Server",
                        risk_tier="TRUSTED_GENERAL",
                        registry_source="contract-source-a",
                        first_seen=now,
                        last_assessed=now,
                    ),
                    McpServerRegistry(
                        server_id="contract-tier-high-risk",
                        name="Contract High Risk Server",
                        risk_tier="HIGH_RISK_ISOLATED",
                        registry_source="contract-source-b",
                        first_seen=now,
                        last_assessed=now,
                    ),
                    McpServerRegistry(
                        server_id="contract-unscored",
                        name="Contract Unscored Server",
                        risk_tier=None,
                        registry_source="contract-source-c",
                        first_seen=now,
                        last_assessed=None,
                    ),
                    McpServerRegistry(
                        server_id="contract-scored-untiered",
                        name="Contract Scored Untiered Server",
                        risk_tier=None,
                        registry_source="contract-source-c",
                        first_seen=now,
                        last_assessed=None,
                    ),
                ]
            )
            session.add_all(
                [
                    McpLlmAxisScore(
                        id=101,
                        server_id="contract-tier-trusted",
                        axis_name="contract-axis",
                        model_version="contract-model",
                    ),
                    McpLlmAxisScore(
                        id=102,
                        server_id="contract-tier-high-risk",
                        axis_name="contract-axis",
                        model_version="contract-model",
                    ),
                    McpLlmAxisScore(
                        id=103,
                        server_id="contract-scored-untiered",
                        axis_name="contract-axis",
                        model_version="contract-model",
                    ),
                ]
            )
            session.commit()

        with TestClient(test_app) as client:
            response = client.get("/api/fleet/tiers")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4, payload
        assert payload["scored_servers"] == 3, payload
        assert payload["unscored_servers"] == 1, payload

        tiers = payload["tiers"]
        assert isinstance(tiers, dict), payload
        assert set(tiers).issubset(ALLOWED_TIERS), tiers
        assert sum(tiers.values()) == 2, tiers

        by_registry_source = payload["by_registry_source"]
        assert isinstance(by_registry_source, dict), payload
        assert by_registry_source["contract-source-a"]["total"] == 1, payload
        assert by_registry_source["contract-source-b"]["total"] == 1, payload
        assert by_registry_source["contract-source-c"]["total"] == 2, payload
        assert isinstance(payload["as_of"], str) and payload["as_of"], payload
        datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00"))
        return True
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)