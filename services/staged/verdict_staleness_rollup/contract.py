from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session

try:
    from app.models import McpServerRegistry as RegistryModel
except ImportError:
    from app.models import McpServerRegistry as RegistryModel

from services.staged.verdict_staleness_rollup.router import router


def _include_router(app: FastAPI) -> None:
    route_paths = [
        route.path
        for route in router.routes
        if getattr(route, "path", "").endswith("/verdict-staleness")
    ]
    if not route_paths:
        raise RuntimeError("verdict-staleness route was not found on the service router")

    route_path = route_paths[0]
    if route_path.startswith("/api/"):
        app.include_router(router)
    elif route_path.startswith("/registry/"):
        app.include_router(router, prefix="/api")
    else:
        app.include_router(router, prefix="/api/registry")


def _seed_registry(engine: Engine) -> None:
    RegistryModel.__table__.create(bind=engine)

    now = datetime.utcnow()
    rows = [
        RegistryModel(
            server_id="server-current",
            name="Current Assessment Server",
            risk_tier="low",
            verdict="trusted",
            trust_score=95.0,
            last_assessed=now,
            first_seen=now - timedelta(days=120),
            last_seen=now,
            scan_count=20,
        ),
        RegistryModel(
            server_id="server-three-days",
            name="Three Day Assessment Server",
            risk_tier="medium",
            verdict="trusted",
            trust_score=80.0,
            last_assessed=now - timedelta(days=3),
            first_seen=now - timedelta(days=90),
            last_seen=now,
            scan_count=15,
        ),
        RegistryModel(
            server_id="server-forty-days",
            name="Forty Day Assessment Server",
            risk_tier="high",
            verdict="trusted",
            trust_score=55.0,
            last_assessed=now - timedelta(days=40),
            first_seen=now - timedelta(days=150),
            last_seen=now,
            scan_count=8,
        ),
        RegistryModel(
            server_id="server-never-assessed",
            name="Never Assessed Server",
            risk_tier=None,
            verdict="trusted",
            trust_score=None,
            last_assessed=None,
            first_seen=now - timedelta(days=7),
            last_seen=now,
            scan_count=2,
        ),
    ]

    with Session(bind=engine) as session:
        session.add_all(rows)
        session.commit()


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _seed_registry(engine)

    app = FastAPI()
    _include_router(app)

    def override_get_session() -> Iterator[Session]:
        session = Session(bind=engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/registry/verdict-staleness")

            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["totals"]["servers"] == 4, payload
            assert payload["totals"]["assessed"] == 3, payload
            assert payload["totals"]["unassessed"] == 1, payload
            assert payload["totals"]["assessment_coverage_pct"] == 75.0, payload

            assert payload["buckets"]["lt_24h"] == 1, payload
            assert payload["buckets"]["d1_7"] == 1, payload
            assert payload["buckets"]["d7_30"] == 0, payload
            assert payload["buckets"]["gt_30d"] == 1, payload
            assert payload["buckets"]["never"] == 1, payload

            assert payload["oldest_unassessed"], payload
            assert (
                payload["oldest_unassessed"][0]["server_id"]
                == "server-never-assessed"
            ), payload

            assert isinstance(payload["by_verdict"], dict), payload
            assert isinstance(payload["by_risk_tier"], dict), payload
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_contract()