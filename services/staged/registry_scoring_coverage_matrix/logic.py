"""logic.py -- scoring coverage computation over the real registry data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


SCORING_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
ALLOWED_AXIS_NAMES = frozenset(SCORING_AXES)


def _utc_naive(value: datetime) -> datetime:
    """Normalize database timestamps for comparisons across SQL backends."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _coverage_rows(db: Session) -> Iterable[tuple]:
    """Return the newest score for each server and allowed axis."""
    return db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at),
        )
        .where(McpLlmAxisScore.axis_name.in_(SCORING_AXES))
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    ).all()


def get_scoring_coverage(db: Session, days: int = 30) -> dict:
    """Compute registry scoring coverage and score staleness."""
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")

    registry_ids = {
        str(server_id)
        for (server_id,) in db.execute(
            select(McpServerRegistry.server_id)
        ).all()
    }
    total_servers = len(registry_ids)

    axes_by_server: Dict[str, set[str]] = {}
    newest_by_server: Dict[str, datetime] = {}

    for server_id, axis_name, newest_scored_at in _coverage_rows(db):
        server_key = str(server_id)
        if server_key not in registry_ids:
            continue

        axes_by_server.setdefault(server_key, set()).add(str(axis_name))
        if newest_scored_at is not None:
            normalized = _utc_naive(newest_scored_at)
            previous = newest_by_server.get(server_key)
            if previous is None or normalized > previous:
                newest_by_server[server_key] = normalized

    scored_server_ids = set(axes_by_server)
    fully_scored_servers = sum(
        1 for server_id in scored_server_ids
        if len(axes_by_server[server_id]) == len(SCORING_AXES)
    )
    partial_servers = sum(
        1 for server_id in scored_server_ids
        if 1 <= len(axes_by_server[server_id]) < len(SCORING_AXES)
    )
    unscored_servers = total_servers - len(scored_server_ids)

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    stale_servers = sum(
        1
        for server_id, newest_scored_at in newest_by_server.items()
        if newest_scored_at < cutoff
    )

    axis_coverage: List[dict] = []
    for axis_name in SCORING_AXES:
        servers_with_axis = sum(
            1 for server_axes in axes_by_server.values()
            if axis_name in server_axes
        )
        coverage_pct = (
            round((servers_with_axis / total_servers) * 100, 2)
            if total_servers
            else 0.0
        )
        axis_coverage.append(
            {
                "axis_name": axis_name,
                "servers_with_axis": servers_with_axis,
                "coverage_pct": coverage_pct,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": total_servers,
        "scored_servers": len(scored_server_ids),
        "unscored_servers": unscored_servers,
        "fully_scored_servers": fully_scored_servers,
        "partial_servers": partial_servers,
        "stale_servers": stale_servers,
        "axis_coverage": axis_coverage,
        "staleness": {
            "stale_after_days": days,
            "stale_servers": stale_servers,
        },
    }


# Alternate descriptive names retained for callers that use a compute-style API.
compute_scoring_coverage = get_scoring_coverage
scoring_coverage = get_scoring_coverage


if __name__ == "__main__":
    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-full", name="Full"),
                McpServerRegistry(server_id="server-partial", name="Partial"),
                McpServerRegistry(server_id="server-unscored-1", name="Unscored 1"),
                McpServerRegistry(server_id="server-unscored-2", name="Unscored 2"),
            ]
        )
        session.flush()
        for index, axis_name in enumerate(SCORING_AXES):
            session.add(
                McpLlmAxisScore(
                    id=index + 1,
                    server_id="server-full",
                    axis_name=axis_name,
                    model_version="test-model",
                )
            )
        for index, axis_name in enumerate(SCORING_AXES[:3], start=100):
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id="server-partial",
                    axis_name=axis_name,
                    model_version="test-model",
                )
            )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/registry/scoring-coverage")
    def scoring_coverage_endpoint(days: int = Query(30, ge=1, le=365)):
        with TestSession() as session:
            return get_scoring_coverage(session, days)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/registry/scoring-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert payload["fully_scored_servers"] == 1
    assert payload["partial_servers"] == 1
    assert payload["unscored_servers"] == 2
    assert {item["axis_name"] for item in payload["axis_coverage"]} == set(
        SCORING_AXES
    )
    print("PASS")