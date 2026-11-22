from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    assessed_at = datetime(2025, 1, 2, 12, 0, 0)
    stale_assessed_at = assessed_at - timedelta(days=1)

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-consistent",
                name="Tier Consistent",
                verdict="safe",
                risk_tier="LOW",
                last_assessed=assessed_at,
            ),
            McpServerRegistry(
                server_id="srv-missing-tier",
                name="Missing Tier",
                verdict="safe",
                risk_tier=None,
                last_assessed=assessed_at,
            ),
            McpServerRegistry(
                server_id="srv-stale",
                name="Stale Verdict",
                verdict="safe",
                risk_tier="LOW",
                last_assessed=stale_assessed_at,
            ),
            McpLlmAxisScore(
                id=1,
                server_id="srv-consistent",
                axis_name="security",
                model_version="contract-model",
                p_top=0.1,
                escalated=False,
                scored_at=assessed_at,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-missing-tier",
                axis_name="security",
                model_version="contract-model",
                p_top=0.2,
                escalated=False,
                scored_at=assessed_at,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv-stale",
                axis_name="security",
                model_version="contract-model",
                p_top=0.3,
                escalated=False,
                scored_at=assessed_at,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/consistency/verdict-tier")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["checked"] == 3, body
    assert body["missing_tier"] == 1, body
    assert body["stale"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)