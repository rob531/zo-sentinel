from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


def _build_test_store() -> tuple[object, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory


def _seed_registry(session: Session) -> None:
    now = datetime.utcnow()
    rows = [
        McpServerRegistry(
            server_id="age-cohort-001",
            name="age-cohort-001",
            first_seen=now - timedelta(days=2),
            last_seen=now,
            risk_tier="low",
            registry_source="contract",
            trust_score=0.95,
            verdict="approved",
        ),
        McpServerRegistry(
            server_id="age-cohort-002",
            name="age-cohort-002",
            first_seen=now - timedelta(days=15),
            last_seen=now,
            risk_tier="high",
            registry_source="contract",
            trust_score=0.25,
            verdict="review",
        ),
        McpServerRegistry(
            server_id="age-cohort-003",
            name="age-cohort-003",
            first_seen=now - timedelta(days=60),
            last_seen=now,
            risk_tier="medium",
            registry_source="contract",
            trust_score=0.60,
            verdict="approved",
        ),
        McpServerRegistry(
            server_id="age-cohort-004",
            name="age-cohort-004",
            first_seen=now - timedelta(days=200),
            last_seen=now,
            risk_tier="low",
            registry_source="contract",
            trust_score=0.90,
            verdict="approved",
        ),
        McpServerRegistry(
            server_id="age-cohort-005",
            name="age-cohort-005",
            first_seen=now - timedelta(days=364),
            last_seen=now,
            risk_tier=None,
            registry_source="contract",
            trust_score=None,
            verdict="unknown",
        ),
    ]
    session.add_all(rows)
    session.commit()


def get_test_session() -> Iterator[Session]:
    engine, factory = _build_test_store()
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def override_get_session() -> Iterator[Session]:
    yield from get_test_session()


def _mount_service_router(that_app: FastAPI) -> None:
    router_module = import_module(
        "services.staged.registry_server_age_cohort_api.router"
    )
    router = getattr(router_module, "router", None)
    if router is None:
        router = getattr(router_module, "api_router", None)
    if router is None:
        raise RuntimeError("registry_server_age_cohort_api.router exports no router")

    paths = [getattr(route, "path", "") for route in router.routes]
    prefix = "" if any(path.startswith("/api/") for path in paths) else "/api"
    that_app.include_router(router, prefix=prefix)


def run_self_test() -> bool:
    engine, factory = _build_test_store()

    with factory() as session:
        _seed_registry(session)

    def override_session() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    _mount_service_router(that_app)
    that_app.dependency_overrides[get_session] = override_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/registry/age-cohorts",
                params={"days": 365},
            )
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 365
            assert payload["total_servers"] == 5

            cohorts = payload["cohorts"]
            assert sum(int(cohort["count"]) for cohort in cohorts) == 5
            assert sum(
                int(cohort.get("unscored_count", 0)) for cohort in cohorts
            ) == 1
            assert any(
                int(cohort.get("unscored_count", 0)) == 1 for cohort in cohorts
            )
            assert any(
                int(count) > 0
                for cohort in cohorts
                for count in cohort["tier_mix"].values()
            )
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")
    return True


def main() -> bool:
    return run_self_test()


if __name__ == "__main__":
    main()