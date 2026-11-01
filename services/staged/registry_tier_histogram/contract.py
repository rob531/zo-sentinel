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

    from .logic import build_tier_histogram
    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(
        McpServerRegistry(
            server_id="srv1",
            name="Trusted One",
            risk_tier="TRUSTED",
            trust_score=90.0,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv2",
            name="Caution One",
            risk_tier="CAUTION",
            trust_score=55.0,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv3",
            name="Trusted Two",
            risk_tier="TRUSTED",
            trust_score=70.0,
        )
    )
    session.add(
        McpServerRegistry(
            server_id="srv4",
            name="Unscored",
            risk_tier=None,
            trust_score=None,
        )
    )
    session.commit()

    logic_result = build_tier_histogram(session)
    assert logic_result["total"] == 4, logic_result
    assert logic_result["unscored"] == 1, logic_result
    trusted = next(
        tier for tier in logic_result["tiers"] if tier["tier"] == "TRUSTED"
    )
    assert trusted["count"] == 2, logic_result
    assert trusted["avg_trust"] == 80.0, logic_result
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    client = TestClient(app)
    response = client.get("/api/registry/tier-histogram")
    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == {
        "total",
        "unscored",
        "tiers",
        "computed_at",
    }, body
    assert body["total"] == 4, body
    assert body["unscored"] == 1, body

    trusted = next(tier for tier in body["tiers"] if tier["tier"] == "TRUSTED")
    assert trusted["count"] == 2, body
    assert trusted["avg_trust"] == 80.0, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)