"""Acceptance self-test for the verdict tier divergence API."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpLlmAxisScore, McpServerRegistry
    from .router import router

    matching_server_id = "server-matching"
    divergent_server_id = "server-divergent"
    assessed_at = datetime.now(timezone.utc)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.metadata.create_all(
        bind=engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id=matching_server_id,
                    name="Matching server",
                    verdict="SAFE",
                    risk_tier="LOW_RISK",
                    trust_score=0.95,
                    confidence=0.95,
                    last_assessed=assessed_at,
                ),
                McpServerRegistry(
                    server_id=divergent_server_id,
                    name="Divergent server",
                    verdict="SAFE",
                    risk_tier="HIGH_RISK_ISOLATED",
                    trust_score=0.95,
                    confidence=0.95,
                    last_assessed=assessed_at,
                ),
                McpLlmAxisScore(
                    id=1,
                    server_id=matching_server_id,
                    axis_name="overall_risk",
                    p_top=0.05,
                    model_version="contract-test",
                    scored_at=assessed_at,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=divergent_server_id,
                    axis_name="overall_risk",
                    p_top=0.05,
                    model_version="contract-test",
                    scored_at=assessed_at,
                ),
            ]
        )
        session.commit()

    try:
        with TestClient(app) as client:
            response = client.get("/api/verdict/tier-divergence")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["checked"] == 2, payload
            assert payload["diverged"] == 1, payload
            assert isinstance(payload["divergence_rate"], float), payload
            divergent_items = [
                item
                for item in payload["items"]
                if item["server_id"] == divergent_server_id
            ]
            assert len(divergent_items) == 1, payload
            assert divergent_items[0]["registry_risk_tier"] == "HIGH_RISK_ISOLATED"
            assert divergent_items[0]["computed_risk_tier"] == "LOW_RISK"
    finally:
        engine.dispose()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)