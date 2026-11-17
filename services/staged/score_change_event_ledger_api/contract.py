from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .router import router


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, class_=Session)

    metadata = MetaData()
    registry_table = Table(
        McpServerRegistry.__tablename__,
        metadata,
        Column("server_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("risk_tier", String, nullable=False),
    )
    change_events_table = Table(
        "mcp_score_change_events",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("axis_name", String, nullable=False),
        Column("old_p_top", Float, nullable=False),
        Column("new_p_top", Float, nullable=False),
        Column("old_label", String, nullable=False),
        Column("new_label", String, nullable=False),
        Column("changed_at", DateTime, nullable=False),
        Column("source", String, nullable=False),
    )
    metadata.create_all(engine)

    now = datetime.utcnow()
    with test_session_factory() as session:
        session.execute(
            insert(registry_table),
            [
                {"server_id": "srv-alpha", "name": "Alpha Server", "risk_tier": "low"},
                {"server_id": "srv-bravo", "name": "Bravo Server", "risk_tier": "high"},
            ],
        )
        session.execute(
            insert(change_events_table),
            [
                {
                    "id": 1,
                    "server_id": "srv-alpha",
                    "axis_name": "reliability",
                    "old_p_top": 0.40,
                    "new_p_top": 0.80,
                    "old_label": "weak",
                    "new_label": "strong",
                    "changed_at": now - timedelta(hours=1),
                    "source": "contract",
                },
                {
                    "id": 2,
                    "server_id": "srv-alpha",
                    "axis_name": "reliability",
                    "old_p_top": 0.70,
                    "new_p_top": 0.45,
                    "old_label": "strong",
                    "new_label": "weak",
                    "changed_at": now - timedelta(hours=2),
                    "source": "contract",
                },
                {
                    "id": 3,
                    "server_id": "srv-bravo",
                    "axis_name": "security",
                    "old_p_top": 0.30,
                    "new_p_top": 0.65,
                    "old_label": "poor",
                    "new_label": "good",
                    "changed_at": now - timedelta(hours=3),
                    "source": "contract",
                },
            ],
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    route_paths = {route.path for route in router.routes}
    if "/api/scores/changes" in route_paths:
        test_app.include_router(router)
    elif "/scores/changes" in route_paths:
        test_app.include_router(router, prefix="/api")
    else:
        raise RuntimeError("score-change router does not expose /api/scores/changes")

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/scores/changes", params={"hours": 168})
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 3, payload

            actual_axes = {
                item["axis_name"]: {
                    "count": item["count"],
                    "up": item["up"],
                    "down": item["down"],
                }
                for item in payload["by_axis"]
            }
            expected_axes = {
                "reliability": {"count": 2, "up": 1, "down": 1},
                "security": {"count": 1, "up": 1, "down": 0},
            }
            assert actual_axes == expected_axes, actual_axes
            assert len(payload["by_axis"]) == 2, payload["by_axis"]

            known_event = next(
                (
                    event
                    for event in payload["recent"]
                    if event["server_id"] == "srv-alpha"
                    and event["axis_name"] == "reliability"
                    and event["old_label"] == "weak"
                    and event["new_label"] == "strong"
                ),
                None,
            )
            assert known_event is not None, payload["recent"]
            assert known_event["server_name"] == "Alpha Server", known_event
            assert known_event["old_p_top"] == 0.40, known_event
            assert known_event["new_p_top"] == 0.80, known_event
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()


def main() -> None:
    run_self_test()
    print("PASS")


_run_self_test = run_self_test

if __name__ == "__main__":
    main()