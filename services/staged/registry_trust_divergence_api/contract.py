from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .router import router


SEED_SERVERS = (
    {
        "server_id": "seed-alpha",
        "name": "Seed Alpha",
        "trust_score": 0.90,
        "p_top": 0.20,
        "verdict": "trusted",
        "risk_tier": "low",
    },
    {
        "server_id": "seed-beta",
        "name": "Seed Beta",
        "trust_score": 0.55,
        "p_top": 0.50,
        "verdict": "review",
        "risk_tier": "medium",
    },
    {
        "server_id": "seed-gamma",
        "name": "Seed Gamma",
        "trust_score": 0.30,
        "p_top": 0.80,
        "verdict": "blocked",
        "risk_tier": "high",
    },
)


def seed_registry(session: Session) -> None:
    for index, seed in enumerate(SEED_SERVERS, start=1):
        session.add(
            McpServerRegistry(
                server_id=seed["server_id"],
                name=seed["name"],
                trust_score=seed["trust_score"],
                verdict=seed["verdict"],
                risk_tier=seed["risk_tier"],
                last_assessed=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.add(
            McpLlmAxisScore(
                id=10_000 + index,
                server_id=seed["server_id"],
                axis_name="overall_risk",
                model_version="seed-model-v1",
                p_top=seed["p_top"],
                scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            )
        )

    session.add(
        McpLlmAxisScore(
            id=9_999,
            server_id="seed-alpha",
            axis_name="overall_risk",
            model_version="seed-model-v0",
            p_top=0.99,
            scored_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    )
    session.commit()


def _include_service_router(application: FastAPI) -> None:
    target_paths = [
        route.path
        for route in router.routes
        if getattr(route, "path", "").rstrip("/").endswith(
            "/registry/trust-divergence"
        )
    ]
    if target_paths and all(path.startswith("/api/") for path in target_paths):
        application.include_router(router)
    else:
        application.include_router(router, prefix="/api")


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    McpServerRegistry.__table__.create(bind=engine)
    McpLlmAxisScore.__table__.create(bind=engine)

    def override_get_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    _include_service_router(that_app)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with SessionLocal() as session:
            seed_registry(session)

        with TestClient(that_app) as client:
            response = client.get(
                "/api/registry/trust-divergence",
                params={"limit": 10},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 3, payload
        assert len(payload["items"]) == 3, payload

        items = payload["items"]
        expected_top = max(
            SEED_SERVERS,
            key=lambda seed: abs(seed["trust_score"] - seed["p_top"]),
        )
        assert items[0]["server_id"] == expected_top["server_id"], payload
        assert items[0]["divergence"] == pytest_approx(
            expected_top["trust_score"] - expected_top["p_top"]
        )
    finally:
        engine.dispose()

    print("PASS")


def pytest_approx(expected: float, tolerance: float = 1e-9) -> object:
    class _Approx:
        def __eq__(self, other: object) -> bool:
            return abs(float(other) - expected) <= tolerance

    return _Approx()


def main() -> None:
    run_self_test()


if __name__ == "__main__":
    main()