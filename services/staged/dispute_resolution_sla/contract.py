from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from services.staged.dispute_resolution_sla.router import router


def get_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _include_router(testing_app: FastAPI) -> None:
    route_paths = {getattr(route, "path", "") for route in router.routes}
    if "/api/disputes/sla" in route_paths:
        testing_app.include_router(router)
    elif "/disputes/sla" in route_paths:
        testing_app.include_router(router, prefix="/api")
    else:
        testing_app.include_router(router, prefix="/api/disputes")


def _self_test() -> None:
    engine, session_factory = get_test_session()
    now = datetime.now(timezone.utc)

    disputes = [
        McpScoreDispute(
            server_id=101,
            submitted_by="contract-user",
            proposed_overall_risk=0.25,
            proposed_axes={"quality": 0.75},
            reason_category="contract_2h",
            status="resolved",
            explanation="Resolved within two hours",
            admin_note="Fast resolution",
            created_at=now - timedelta(hours=3),
            resolved_at=now - timedelta(hours=1),
        ),
        McpScoreDispute(
            server_id=102,
            submitted_by="contract-user",
            proposed_overall_risk=0.50,
            proposed_axes={"quality": 0.50},
            reason_category="contract_5d",
            status="resolved",
            explanation="Resolved within five days",
            admin_note="Resolution completed",
            created_at=now - timedelta(days=5),
            resolved_at=now,
        ),
        McpScoreDispute(
            server_id=103,
            submitted_by="contract-user",
            proposed_overall_risk=0.75,
            proposed_axes={"quality": 0.25},
            reason_category="contract_open",
            status="open",
            explanation="Awaiting administrator review",
            admin_note=None,
            created_at=now - timedelta(hours=1),
            resolved_at=None,
        ),
    ]

    session = session_factory()
    try:
        session.add_all(disputes)
        session.commit()
    finally:
        session.close()

    testing_app = FastAPI()
    _include_router(testing_app)

    def override_get_session():
        override_session = session_factory()
        try:
            yield override_session
        finally:
            override_session.close()

    testing_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(testing_app) as client:
            response = client.get("/api/disputes/sla", params={"days": 30})

        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["days"] == 30
        assert payload["total"] == 3
        assert payload["open_count"] == 1
        assert payload["resolved_count"] == 2
        assert payload["median_hours"] > 0

        buckets = {bucket["reason_category"]: bucket for bucket in payload["buckets"]}
        assert buckets["contract_2h"]["under_24h"] == 1
        assert buckets["contract_5d"]["d1_to_7d"] == 1
        assert buckets["contract_open"]["open"] == 1

        assert payload["overall"]["under_24h"] == 1
        assert payload["overall"]["d1_to_7d"] == 1
        assert payload["overall"]["over_7d"] == 0
    finally:
        engine.dispose()


def run_self_test() -> None:
    _self_test()


if __name__ == "__main__":
    run_self_test()
    print("PASS")