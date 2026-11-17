"""Acceptance self-test for the dispute backlog queue service."""

from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

try:  # The project historically exposed models from app.model.
    from app.model import Base as ModelBase
except ImportError:  # Support deployments that use the plural module name.
    from app.models import Base as ModelBase


DISPUTE_TABLE = "mcp_score_disputes"
REGISTRY_TABLE = "mcp_server_registry"


def _mapped_class(table_name: str) -> type[Any]:
    for mapper in ModelBase.registry.mappers:
        if any(table.name == table_name for table in mapper.tables):
            return mapper.class_
    raise RuntimeError(f"No mapped model was found for {table_name}")


def _build_app() -> FastAPI:
    logic = import_module("services.staged.dispute_backlog_queue.logic")
    router = getattr(logic, "router", None)
    if router is None:
        router = getattr(logic, "api_router", None)
    if router is None:
        raise RuntimeError("The dispute backlog logic module does not expose a router")

    test_app = FastAPI(title="dispute_backlog_queue contract")
    route_paths = [
        getattr(route, "path", "")
        for route in getattr(router, "routes", [])
        if getattr(route, "path", "")
    ]
    has_api_prefix = any(
        path == "/api" or path.startswith("/api/") for path in route_paths
    )
    test_app.include_router(router, prefix="" if has_api_prefix else "/api")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )

    dispute_model = _mapped_class(DISPUTE_TABLE)
    registry_model = _mapped_class(REGISTRY_TABLE)
    ModelBase.metadata.create_all(
        bind=engine,
        tables=[
            ModelBase.metadata.tables[REGISTRY_TABLE],
            ModelBase.metadata.tables[DISPUTE_TABLE],
        ],
    )

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.add_all(
            [
                registry_model(
                    server_id="srv-alpha",
                    name="Alpha Registry",
                    risk_tier="high",
                ),
                registry_model(
                    server_id="srv-beta",
                    name="Beta Registry",
                    risk_tier="medium",
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                dispute_model(
                    id=101,
                    server_id="srv-alpha",
                    submitted_by="analyst.one",
                    proposed_overall_risk="high",
                    proposed_axes={"safety": 0.91, "privacy": 0.87},
                    reason_category="false_positive",
                    explanation="Automated score appears incorrect for this server.",
                    status="pending",
                    admin_note=None,
                    created_at=now - timedelta(days=2),
                    resolved_at=None,
                ),
                dispute_model(
                    id=102,
                    server_id="srv-beta",
                    submitted_by="analyst.two",
                    proposed_overall_risk="medium",
                    proposed_axes={"safety": 0.62},
                    reason_category="missing_context",
                    explanation="The registry context does not reflect current usage.",
                    status="pending",
                    admin_note=None,
                    created_at=now - timedelta(days=5),
                    resolved_at=None,
                ),
                dispute_model(
                    id=103,
                    server_id="srv-alpha",
                    submitted_by="analyst.three",
                    proposed_overall_risk="low",
                    proposed_axes={"safety": 0.20},
                    reason_category="score_mismatch",
                    explanation="Previously submitted score needs review.",
                    status="resolved",
                    admin_note="Adjusted after review.",
                    created_at=now - timedelta(days=12),
                    resolved_at=now - timedelta(days=10),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    return test_app


def run_contract() -> None:
    app = _build_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/disputes/backlog",
            params={"status": "pending", "limit": 50},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 2, payload
        assert len(payload["items"]) == 2, payload

        pending_items = {
            item["submitted_by"]: item
            for item in payload["items"]
        }
        assert (
            pending_items["analyst.one"]["server_name"] == "Alpha Registry"
        ), payload
        assert pending_items["analyst.two"]["server_name"] == "Beta Registry", payload

        summary_response = client.get("/api/disputes/backlog/summary")
        assert summary_response.status_code == 200, summary_response.text
        summary = summary_response.json()
        assert "pending" in summary["by_status"], summary
        assert summary["by_status"]["pending"] == 2, summary


if __name__ == "__main__":
    run_contract()
    print("PASS")