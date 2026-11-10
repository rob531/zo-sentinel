"""Acceptance self-test for the freshness_surface_stats service."""

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
    Base.metadata.create_all(
        engine,
        tables=[FreshnessStat.__table__, McpServerRegistry.__table__],
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = testing_session()
    try:
        session.add_all(
            [
                FreshnessStat(
                    surface="github",
                    payload={"registry_source": "github", "records": 4},
                    computed_at=now - timedelta(seconds=7200),
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="npm",
                    payload={"registry_source": "npm", "records": 2},
                    computed_at=now - timedelta(seconds=30),
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="pypi",
                    payload={"registry_source": "pypi", "records": 1},
                    computed_at=now - timedelta(seconds=300),
                    compute_seconds=0.75,
                ),
                McpServerRegistry(
                    server_id="registry-github-1",
                    name="github-one",
                    registry_source="github",
                ),
                McpServerRegistry(
                    server_id="registry-github-2",
                    name="github-two",
                    registry_source="github",
                ),
                McpServerRegistry(
                    server_id="registry-npm-1",
                    name="npm-one",
                    registry_source="npm",
                ),
                McpServerRegistry(
                    server_id="registry-pypi-1",
                    name="pypi-one",
                    registry_source="pypi",
                ),
            ]
        )
        session.commit()
    finally:
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
    response = client.get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text

    body = response.json()
    surfaces = body["surfaces"]
    assert len(surfaces) == 3, body
    assert sum(1 for item in surfaces if bool(item.get("stale"))) == 1, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)