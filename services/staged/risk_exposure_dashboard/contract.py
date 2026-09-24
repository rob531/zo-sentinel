from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.models import McpLlmAxisScore, McpServerRegistry, Org

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as session:
        orgs = [Org(id=str(index), name=f"Org {index}") for index in range(1, 4)]
        session.add_all(orgs)
        for index, tier in enumerate(("low", "medium", "high"), start=1):
            session.add(
                McpServerRegistry(
                    server_id=str(index),
                    name=f"Server {index}",
                    risk_tier=tier,
                )
            )
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id=str(index),
                    axis_name="test_axis",
                    model_version="test-v1",
                )
            )
        session.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get("/api/risk/exposure")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list) and len(payload) == 3, payload
    assert {entry["org_id"] for entry in payload} == {1, 2, 3}, payload
    assert all(len(entry["tier_counts"]) == 1 for entry in payload), payload
    org_one = next(entry for entry in payload if entry["org_id"] == 1)
    assert org_one["tier_counts"].get("low") == 1, org_one
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)