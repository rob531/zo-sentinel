"""Acceptance self-test for the freshness surface registry API."""
from __future__ import annotations

import sys
from datetime import datetime


def run() -> bool:
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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add(
        FreshnessStat(
            surface="fresh_surface",
            payload={"max_age": 3600, "source": "live"},
            computed_at=datetime.utcnow(),
            compute_seconds=0.25,
        )
    )
    session.add(
        FreshnessStat(
            surface="stale_surface",
            payload={"max_age": 3600, "source": "cached"},
            computed_at=datetime(2000, 1, 1),
            compute_seconds=1.5,
        )
    )
    session.add(
        FreshnessStat(
            surface="empty_surface",
            payload={},
            computed_at=datetime(2000, 1, 1),
            compute_seconds=0.0,
        )
    )
    session.commit()
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

    client = TestClient(app)
    response = client.get("/api/freshness/surfaces")
    assert response.status_code == 200, response.text

    body = response.json()
    surfaces = body["surfaces"]
    assert len(surfaces) == 3, body

    by_surface = {item["surface"]: item for item in surfaces}
    assert by_surface["stale_surface"]["stale"] is True, body
    assert by_surface["fresh_surface"]["stale"] is False, body
    for item in surfaces:
        assert "surface" in item, body
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