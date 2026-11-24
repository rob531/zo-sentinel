"""Acceptance self-test for the staged verdict reasoning quality audit service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add(
        McpServerRegistry(
            server_id="verdict-reasoned",
            verdict="HEALTHY",
            verdict_reasoning="This server has a sufficiently detailed explanation of its current verdict.",
            confidence=0.95,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="verdict-blank",
            verdict="BLANK_REASONING",
            verdict_reasoning="   ",
            confidence=0.80,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="verdict-short",
            verdict="SHORT_REASONING",
            verdict_reasoning="12345",
            confidence=0.70,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="verdict-no-confidence",
            verdict="NO_CONFIDENCE",
            verdict_reasoning="This server has a sufficiently detailed explanation of its current verdict.",
            confidence=None,
        )
    )
    session.commit()
    session.close()

    def _override():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    with TestClient(app) as client:
        response = client.get("/api/quality/verdict-reasoning")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["total_servers"] == 4, body
    buckets = {bucket["verdict"]: bucket for bucket in body["by_verdict"]}
    assert buckets["BLANK_REASONING"]["missing_reasoning"] == 1, body
    assert buckets["SHORT_REASONING"]["hollow_reasoning"] == 1, body
    assert buckets["NO_CONFIDENCE"]["missing_confidence"] == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)