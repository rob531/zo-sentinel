"""Contract self-test for the registry trust-tier divergence service."""
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
            server_id="high-critical",
            name="High Trust / Critical Risk",
            trust_score=90,
            risk_tier="critical",
        )
    )
    session.add(
        McpServerRegistry(
            server_id="consistent",
            name="Consistent Server",
            trust_score=60,
            risk_tier="caution",
        )
    )
    session.add(
        McpServerRegistry(
            server_id="low-trusted",
            name="Low Trust / Trusted Risk",
            trust_score=10,
            risk_tier="trusted",
        )
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
    response = client.get("/api/registry/trust-tier-divergence")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 3, body
    assert body["divergent_count"] == 2, body

    divergent = next(
        row for row in body["servers"]
        if row["server_id"] == "high-critical"
    )
    assert divergent["divergent"] is True, body
    assert divergent["expected_band"] == "high", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)