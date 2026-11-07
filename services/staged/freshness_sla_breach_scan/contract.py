"""Acceptance self-test for the freshness SLA breach scan service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, FreshnessStat, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    servers = (
        ("srv1", "Fresh Server", "HIGH", now - timedelta(hours=1)),
        ("srv2", "Stale Server", "MEDIUM", now - timedelta(hours=48)),
        ("srv3", "Oldest Server", "LOW", now - timedelta(hours=200)),
    )

    session = testing_session()
    try:
        for server_id, name, risk_tier, last_assessed in servers:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    risk_tier=risk_tier,
                    trust_score=50,
                    last_assessed=last_assessed,
                    last_seen=last_assessed,
                )
            )
            session.add(
                FreshnessStat(
                    surface=server_id,
                    payload={
                        "server_id": server_id,
                        "name": name,
                        "risk_tier": risk_tier,
                        "last_assessed": last_assessed.isoformat(),
                    },
                    computed_at=now,
                )
            )
        session.commit()
    finally:
        session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    response = TestClient(app).get("/api/freshness/sla/breaches")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_age_hours"] == 24, body
    assert body["breach_count"] == 2, body
    assert len(body["servers"]) == 2, body
    assert body["servers"][0]["server_id"] == "srv3", body
    assert body["servers"][0]["age_hours"] >= body["servers"][1]["age_hours"], body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)