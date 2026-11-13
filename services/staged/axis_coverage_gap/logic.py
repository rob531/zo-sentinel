"""Axis coverage computation over the real application data layer."""
from __future__ import annotations

from typing import Dict, List

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_axis_coverage(db: Session) -> Dict:
    """Return axis coverage gaps for all registered servers."""
    registry_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
    ).all()
    score_rows = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    ).all()

    scored_axes: Dict[object, set[str]] = {}
    for server_id, axis_name in score_rows:
        if axis_name in CANONICAL_AXES:
            scored_axes.setdefault(server_id, set()).add(axis_name)

    servers = []
    for server_id, risk_tier in registry_rows:
        present = scored_axes.get(server_id, set())
        missing = [axis for axis in CANONICAL_AXES if axis not in present]
        servers.append(
            {
                "server_id": server_id,
                "risk_tier": risk_tier,
                "missing_axes": missing,
                "missing_count": len(missing),
            }
        )

    total_servers = len(servers)
    covered_fully = sum(server["missing_count"] == 0 for server in servers)
    coverage_pct = (
        round((covered_fully / total_servers) * 100, 2) if total_servers else 0.0
    )

    axes_missing_histogram = {
        axis: sum(axis in server["missing_axes"] for server in servers)
        for axis in CANONICAL_AXES
    }

    gap_servers = [server for server in servers if server["missing_count"]]
    gap_servers.sort(key=lambda server: (server["missing_count"], str(server["server_id"])))
    gap_servers = gap_servers[:25]

    return {
        "total_servers": total_servers,
        "covered_fully": covered_fully,
        "coverage_pct": coverage_pct,
        "axes_missing_histogram": axes_missing_histogram,
        "gap_servers": gap_servers,
    }


def axis_coverage_report(db: Session) -> Dict:
    """Compatibility alias for callers using the report-oriented name."""
    return get_axis_coverage(db)


def axis_coverage(db: Session = Depends(get_session)) -> Dict:
    """FastAPI dependency entry point for the axis coverage report."""
    return get_axis_coverage(db)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        McpServerRegistry.__table__.create(connection)
        McpLlmAxisScore.__table__.create(connection)

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="fully-scored", risk_tier="low"),
                McpServerRegistry(server_id="partially-scored", risk_tier="high"),
                McpServerRegistry(server_id="unscored", risk_tier="unknown"),
            ]
        )
        session.flush()

        score_id = 1
        for server_id, axes in (
            (
                "fully-scored",
                CANONICAL_AXES,
            ),
            (
                "partially-scored",
                CANONICAL_AXES[:3],
            ),
        ):
            for axis_name in axes:
                session.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=server_id,
                        axis_name=axis_name,
                        model_version="self-test",
                        p_top=0.5,
                    )
                )
                score_id += 1
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/scores/axis-coverage")
    def read_axis_coverage(db: Session = Depends(get_session)):
        return get_axis_coverage(db)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/scores/axis-coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 3
    assert any(
        server["server_id"] == "partially-scored"
        and server["missing_count"] == 4
        for server in payload["gap_servers"]
    )
    assert all(
        server["server_id"] != "fully-scored" for server in payload["gap_servers"]
    )
    print("PASS")


if __name__ == "__main__":
    _self_test()