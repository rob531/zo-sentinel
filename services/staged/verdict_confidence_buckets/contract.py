"""Acceptance self-test for the verdict confidence buckets service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(
        McpServerRegistry(
            server_id="confidence-low",
            name="Low confidence server",
            verdict="APPROVE",
            risk_tier="LOW",
            confidence=0.2,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="confidence-medium",
            name="Medium confidence server",
            verdict="REVIEW",
            risk_tier="MEDIUM",
            confidence=0.5,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="confidence-very-high",
            name="Very high confidence server",
            verdict="REJECT",
            risk_tier="HIGH",
            confidence=0.9,
        )
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/verdict/confidence-buckets")
        assert response.status_code == 200, response.text
        body = response.json()

    assert len(body["bands"]) == 4, body
    medium = next(band for band in body["bands"] if band["band"] == "MEDIUM")
    assert medium["count"] == 1, body
    assert body["total_servers"] == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)