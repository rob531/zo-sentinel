# deps: fastapi, pydantic, sqlalchemy
"""contract.py -- acceptance self-test for org_risk_tier_distribution."""
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
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    s = TestingSession()
    s.add(McpServerRegistry(server_id="c1", org_id="org1", risk_tier="low"))
    s.add(McpServerRegistry(server_id="c2", org_id="org1", risk_tier="low"))
    s.add(McpServerRegistry(server_id="c3", org_id="org1", risk_tier="high"))
    s.add(McpServerRegistry(server_id="c4", org_id="org2", risk_tier="medium"))
    s.add(McpServerRegistry(server_id="c5", org_id="org3", risk_tier=None))
    s.commit()
    s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    c = TestClient(app)
    r = c.get("/api/orgs/risk-tier-distribution")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_servers"] == 5, body

    by_org = {}
    for item in body["items"]:
        by_org.setdefault(item["org_id"], {})[item["risk_tier"]] = item["count"]

    assert by_org.get("org1", {}).get("low") == 2, body
    assert by_org.get("org1", {}).get("high") == 1, body
    assert by_org.get("org2", {}).get("medium") == 1, body
    assert by_org.get("org3", {}).get("unknown") == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
