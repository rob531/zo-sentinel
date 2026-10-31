from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from services.staged.freshness_surface_lag.router import router


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.utcnow()
    stale_time = now - timedelta(seconds=7200)
    recent_time = now - timedelta(seconds=30)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    id INTEGER PRIMARY KEY,
                    surface VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    computed_at DATETIME NOT NULL,
                    compute_seconds FLOAT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            [
                {
                    "surface": "alpha",
                    "payload": json.dumps({"momentum": 0.25, "source_count": 2}),
                    "computed_at": stale_time,
                    "compute_seconds": 0.4,
                },
                {
                    "surface": "beta",
                    "payload": json.dumps(
                        {"momentum": 1.5, "signal_count": 7, "status": "fresh"}
                    ),
                    "computed_at": now,
                    "compute_seconds": 0.8,
                },
                {
                    "surface": "beta",
                    "payload": json.dumps({"momentum": 0.75, "signal_count": 4}),
                    "computed_at": recent_time,
                    "compute_seconds": 0.6,
                },
            ],
        )

    session = TestingSessionLocal()

    def override_get_session() -> Iterator[Session]:
        yield session

    application = FastAPI()
    route_paths = {getattr(route, "path", "") for route in router.routes}
    if "/api/freshness/surface-lag" not in route_paths:
        application.include_router(router, prefix="/api")
    else:
        application.include_router(router)

    application.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(application) as client:
            response = client.get("/api/freshness/surface-lag")

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["surfaces"]) == 2, body
        assert any(
            "momentum" in surface["payload_keys"] for surface in body["surfaces"]
        ), body
        assert body["stale_count"] == 1, body
    finally:
        application.dependency_overrides.clear()
        session.close()
        engine.dispose()

    print("PASS")


def run_contract_test() -> None:
    run_self_test()


def main() -> None:
    run_self_test()


if __name__ == "__main__":
    main()