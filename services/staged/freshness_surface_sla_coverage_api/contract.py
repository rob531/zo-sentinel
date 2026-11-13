"""Acceptance self-test for the freshness surface SLA coverage API."""
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
    from app.models import Base, FreshnessStat

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False)

    now = datetime.utcnow()
    session = testing_session()
    try:
        session.add_all(
            [
                FreshnessStat(
                    surface="fresh_primary",
                    payload={"primary": {"value": 1}},
                    computed_at=now - timedelta(hours=1),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="stale_primary",
                    payload={"primary": {"value": 2}},
                    computed_at=now - timedelta(hours=48),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="fresh_secondary",
                    payload={"secondary": {"value": 3}},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.5,
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

    with TestClient(app) as client:
        response = client.get("/api/freshness/surfaces/coverage")
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["stale_after_hours"] == 24, body
        assert len(body["surfaces"]) == 3, body
        assert sum(1 for item in body["surfaces"] if item["stale"] is True) == 1, body
        for item in body["surfaces"]:
            assert "surface" in item, body
            assert "computed_at" in item, body
            assert "age_seconds" in item, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)