"""Acceptance self-test for the freshness surface timeline API."""
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
    from app.models import Base, FreshnessStat

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = testing_session()
    session.add(
        FreshnessStat(
            surface="surface-alpha",
            payload={"source": "contract"},
            computed_at=now - timedelta(hours=1),
            compute_seconds=12.5,
        )
    )
    session.add(
        FreshnessStat(
            surface="surface-beta",
            payload={"source": "contract"},
            computed_at=now - timedelta(hours=30),
            compute_seconds=7.25,
        )
    )
    session.add(
        FreshnessStat(
            surface="surface-gamma",
            payload={"source": "contract"},
            computed_at=now - timedelta(hours=2),
            compute_seconds=3.75,
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

    response = client.get("/api/freshness/surface-timeline", params={"days": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 2, body
    surfaces = {item["surface"]: item for item in body["surfaces"]}
    assert set(surfaces) == {"surface-alpha", "surface-beta", "surface-gamma"}, body

    alpha = surfaces["surface-alpha"]
    assert len(alpha["points"]) == 1, body
    point = alpha["points"][0]
    assert point["compute_seconds"] == 12.5, body
    assert point["gap_seconds"] == 0.0, body
    assert alpha["stale"] is False, body

    beta = surfaces["surface-beta"]
    assert beta["stale"] is True, body

    fresh_response = client.get(
        "/api/freshness/surface-timeline",
        params={"days": 2, "stale_hours": 48},
    )
    assert fresh_response.status_code == 200, fresh_response.text
    fresh_surfaces = {
        item["surface"]: item for item in fresh_response.json()["surfaces"]
    }
    assert fresh_surfaces["surface-beta"]["stale"] is False, fresh_response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)