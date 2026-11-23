"""Acceptance self-test for the registry verdict source matrix service."""
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
    session.add_all(
        [
            McpServerRegistry(
                server_id="matrix-github-high",
                name="GitHub High",
                registry_source="github",
                verdict="MALICIOUS",
                risk_tier="HIGH",
                trust_score=10.0,
            ),
            McpServerRegistry(
                server_id="matrix-github-medium",
                name="GitHub Medium",
                registry_source="github",
                verdict="SAFE",
                risk_tier="MEDIUM",
                trust_score=70.0,
            ),
            McpServerRegistry(
                server_id="matrix-npm-low",
                name="NPM Low",
                registry_source="npm",
                verdict=None,
                risk_tier="LOW",
                trust_score=95.0,
            ),
            McpServerRegistry(
                server_id="matrix-npm-unscored",
                name="NPM Unscored",
                registry_source="npm",
                verdict="UNKNOWN",
                risk_tier=None,
                trust_score=None,
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
    response = client.get("/api/registry/verdict-source-matrix")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "sources" in body, body
    assert "generated_at" in body, body

    sources = body["sources"]
    source_names = {entry["source"] for entry in sources}
    assert source_names == {"github", "npm"}, body

    unscored_count = 0
    for source in sources:
        assert "tiers" in source, source
        assert "total" in source, source
        for tier in source["tiers"]:
            assert tier["count"] >= 0, body
            assert 0 <= tier["pct"] <= 100, body
            if tier["tier"] == "UNSCORED":
                unscored_count += tier["count"]

    assert unscored_count == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- acceptance failures must be loud
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)