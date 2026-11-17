from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute
from services.staged.dispute_submitter_queue_api.router import router


def _include_service_router(that_app: FastAPI) -> None:
    route_paths = {getattr(route, "path", "") for route in router.routes}
    if any(path == "/api" or path.startswith("/api/") for path in route_paths):
        that_app.include_router(router)
    else:
        that_app.include_router(router, prefix="/api")


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine, checkfirst=True)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    _include_service_router(that_app)
    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seed_rows = [
        {
            "server_id": 1,
            "submitted_by": "alice",
            "proposed_overall_risk": 0.8,
            "proposed_axes": {"harmful": 0.9},
            "reason_category": "incorrect_score",
            "explanation": "Alice reported an incorrect open dispute.",
            "status": "open",
            "created_at": now - timedelta(hours=2),
            "resolved_at": None,
        },
        {
            "server_id": 1,
            "submitted_by": "alice",
            "proposed_overall_risk": 0.6,
            "proposed_axes": {"harmful": 0.7},
            "reason_category": "incorrect_score",
            "explanation": "Alice reported a previously resolved dispute.",
            "status": "resolved",
            "created_at": now - timedelta(hours=5),
            "resolved_at": now - timedelta(hours=4),
        },
        {
            "server_id": 2,
            "submitted_by": "bob",
            "proposed_overall_risk": 0.4,
            "proposed_axes": {"harmful": 0.5},
            "reason_category": "incorrect_score",
            "explanation": "Bob reported an incorrect open dispute.",
            "status": "open",
            "created_at": now - timedelta(hours=1),
            "resolved_at": None,
        },
        {
            "server_id": 2,
            "submitted_by": "bob",
            "proposed_overall_risk": 0.3,
            "proposed_axes": {"harmful": 0.2},
            "reason_category": "incorrect_score",
            "explanation": "Bob reported a previously resolved dispute.",
            "status": "resolved",
            "created_at": now - timedelta(hours=3),
            "resolved_at": now - timedelta(hours=2),
        },
    ]

    try:
        with session_factory() as session:
            session.add_all(McpScoreDispute(**row) for row in seed_rows)
            session.commit()

        expected_non_resolved = sum(
            1 for row in seed_rows if row["status"] != "resolved"
        )

        with TestClient(that_app) as client:
            response = client.get(
                "/api/disputes/submitters",
                params={"status": "", "days": 30},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == ""
        assert payload["days"] == 30
        assert len(payload["submitters"]) == 2
        assert {
            submitter["submitted_by"] for submitter in payload["submitters"]
        } == {"alice", "bob"}
        assert sum(
            submitter["open_disputes"] for submitter in payload["submitters"]
        ) == expected_non_resolved
        assert payload["total_disputes"] == len(seed_rows)
    finally:
        engine.dispose()


def main() -> None:
    run_contract()
    print("PASS")


if __name__ == "__main__":
    main()