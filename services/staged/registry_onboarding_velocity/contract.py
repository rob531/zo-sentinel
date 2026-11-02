"""Acceptance self-test for the registry onboarding velocity service."""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .logic import build_velocity_series
    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    today = datetime.now().date()
    seeded_rows: list[dict] = []
    session = testing_session()
    try:
        for day_offset in range(3):
            day = today - timedelta(days=day_offset)
            for index, source in enumerate(("github", "npm")):
                observed_at = datetime.combine(day, time(hour=12))
                risk_tier = "HIGH" if index == 0 else None
                session.add(
                    McpServerRegistry(
                        server_id=f"velocity-{day_offset}-{index}",
                        name=f"Velocity Server {day_offset}-{index}",
                        registry_source=source,
                        first_seen=observed_at,
                        last_seen=observed_at,
                        last_scanned=observed_at,
                        risk_tier=risk_tier,
                    )
                )
                seeded_rows.append(
                    {
                        "first_seen": observed_at,
                        "last_scanned": observed_at,
                        "registry_source": source,
                        "risk_tier": risk_tier,
                    }
                )
        session.commit()
    finally:
        session.close()

    helper_series = build_velocity_series(seeded_rows, 3)
    assert len(helper_series) == 3, helper_series
    assert sum(item["first_seen_count"] for item in helper_series) == len(
        seeded_rows
    ), helper_series

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    route_paths = {getattr(route, "path", "") for route in router.routes}
    if (
        "/registry/onboarding-velocity" in route_paths
        and "/api/registry/onboarding-velocity" not in route_paths
    ):
        app.include_router(router, prefix="/api")
    else:
        app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get(
        "/api/registry/onboarding-velocity", params={"days": 3}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == 3, body
    series = body["series"]
    assert len(series) == 3, body
    assert sum(item["first_seen_count"] for item in series) == len(
        seeded_rows
    ), body
    populated = [item for item in series if item["first_seen_count"] > 0]
    assert populated, body
    assert all(item["distinct_sources"] == 2 for item in populated), body
    assert body["totals"]["servers"] == len(seeded_rows), body
    assert body["totals"]["sources"] == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)