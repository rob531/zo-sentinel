from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.verdict_mix_distribution_api.router import router


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="verdict-mix-contract-trusted",
                    name="trusted",
                    risk_tier="trusted",
                    verdict="approved",
                    trust_score=90.0,
                    last_assessed=now - timedelta(hours=2),
                ),
                McpServerRegistry(
                    server_id="verdict-mix-contract-elevated",
                    name="elevated",
                    risk_tier="elevated",
                    verdict="restricted",
                    trust_score=40.0,
                    last_assessed=now - timedelta(days=3),
                ),
                McpServerRegistry(
                    server_id="verdict-mix-contract-critical",
                    name="critical",
                    risk_tier="critical",
                    verdict="unscored",
                    trust_score=None,
                    last_assessed=now - timedelta(days=3),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    route_paths = {
        route.path
        for route in router.routes
        if hasattr(route, "path")
    }
    if "/api/verdict-mix" in route_paths:
        test_app.include_router(router)
    elif "/verdict-mix" in route_paths:
        test_app.include_router(router, prefix="/api")
    else:
        raise RuntimeError("verdict-mix route is not exposed by the service router")

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/verdict-mix")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 3, payload
            assert payload["assessed_24h"] == 1, payload

            critical = next(
                row for row in payload["by_risk_tier"] if row["tier"] == "critical"
            )
            assert critical["count"] == 1, payload

            unscored = next(
                row for row in payload["by_verdict"] if row["verdict"] == "unscored"
            )
            assert unscored["count"] == 1, payload
            assert unscored["avg_trust_score"] is None, payload
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    run_self_test()
    print("PASS")