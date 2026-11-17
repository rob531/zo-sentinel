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
    from app.models import FreshnessStat

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    session = TestingSession()
    try:
        session.add_all(
            [
                FreshnessStat(
                    surface="fresh_alpha",
                    payload={"sla_seconds": 3600},
                    computed_at=now,
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="fresh_beta",
                    payload={"sla_seconds": 3600},
                    computed_at=now,
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="stale_surface",
                    payload={"sla_seconds": 3600},
                    computed_at=now - timedelta(seconds=7200),
                    compute_seconds=3.75,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["surfaces"]) == 3, body
        assert all(
            isinstance(surface["age_seconds"], int)
            and not isinstance(surface["age_seconds"], bool)
            and surface["age_seconds"] >= 0
            for surface in body["surfaces"]
        ), body

        stale_response = client.get(
            "/api/freshness/surfaces",
            params={"stale_only": "true"},
        )
        assert stale_response.status_code == 200, stale_response.text
        stale_body = stale_response.json()
        stale_surfaces = stale_body["surfaces"]
        assert len(stale_surfaces) == 1, stale_body
        assert stale_surfaces[0]["surface"] == "stale_surface", stale_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)