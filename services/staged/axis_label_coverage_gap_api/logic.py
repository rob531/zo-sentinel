from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text

try:
    from app.db import SessionLocal
except ModuleNotFoundError:
    repository_root = Path(__file__).resolve().parents[3]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from app.db import SessionLocal


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class ServerAxisCoverage(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    present_axes: List[str]
    missing_axes: List[str]
    present_count: int
    latest_scored_at: Optional[datetime]


class AxisLabelCoverageResponse(BaseModel):
    total_servers: int
    fully_scored: int
    partial: int
    servers: List[ServerAxisCoverage]


class AxisCoverageServer(ServerAxisCoverage):
    pass


class AxisCoverageResponse(AxisLabelCoverageResponse):
    pass


def get_axis_label_coverage(
    server_id: Optional[str] = None,
    min_axes: int = 1,
) -> AxisLabelCoverageResponse:
    if min_axes < 1 or min_axes > len(CANONICAL_AXES):
        raise HTTPException(
            status_code=422,
            detail=f"min_axes must be between 1 and {len(CANONICAL_AXES)}",
        )

    query = text(
        """
        SELECT
            registry.server_id AS server_id,
            registry.name AS name,
            registry.risk_tier AS risk_tier,
            scores.axis_name AS axis_name,
            scores.label AS label,
            scores.p_top AS p_top,
            scores.scored_at AS scored_at
        FROM mcp_server_registry AS registry
        LEFT JOIN mcp_llm_axis_scores AS scores
            ON scores.server_id = registry.server_id
        WHERE (:server_id IS NULL OR registry.server_id = :server_id)
        ORDER BY registry.server_id, scores.axis_name, scores.scored_at
        """
    )

    grouped: Dict[str, Dict[str, object]] = {}

    with SessionLocal() as session:
        rows = session.execute(
            query,
            {"server_id": server_id},
        ).mappings().all()

        for row in rows:
            row_server_id = str(row["server_id"])
            server = grouped.setdefault(
                row_server_id,
                {
                    "name": row["name"],
                    "risk_tier": row["risk_tier"],
                    "axes": set(),
                    "latest_scored_at": None,
                },
            )

            axis_name = row["axis_name"]
            if axis_name is None:
                continue

            axis_name = str(axis_name)
            if axis_name in CANONICAL_AXES:
                server["axes"].add(axis_name)

            scored_at = row["scored_at"]
            if scored_at is not None:
                current_latest = server["latest_scored_at"]
                if current_latest is None or scored_at > current_latest:
                    server["latest_scored_at"] = scored_at

    servers: List[ServerAxisCoverage] = []
    fully_scored = 0

    for row_server_id in sorted(grouped):
        server = grouped[row_server_id]
        present_axes = [
            axis for axis in CANONICAL_AXES if axis in server["axes"]
        ]
        missing_axes = [
            axis for axis in CANONICAL_AXES if axis not in server["axes"]
        ]
        present_count = len(present_axes)

        if present_count < min_axes:
            continue

        if present_count == len(CANONICAL_AXES):
            fully_scored += 1

        servers.append(
            ServerAxisCoverage(
                server_id=row_server_id,
                name=str(server["name"]),
                risk_tier=str(server["risk_tier"]),
                present_axes=present_axes,
                missing_axes=missing_axes,
                present_count=present_count,
                latest_scored_at=server["latest_scored_at"],
            )
        )

    return AxisLabelCoverageResponse(
        total_servers=len(servers),
        fully_scored=fully_scored,
        partial=len(servers) - fully_scored,
        servers=servers,
    )


axis_label_coverage = get_axis_label_coverage
compute_axis_label_coverage = get_axis_label_coverage


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite+pysqlite:///:memory:")
    test_session_factory = sessionmaker(bind=engine)

    with test_session_factory() as session:
        session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_tier TEXT NOT NULL
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    p_top REAL,
                    scored_at TIMESTAMP
                )
                """
            )
        )

        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier)
                VALUES
                    (:server_id, :name, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-one",
                    "name": "One",
                    "risk_tier": "high",
                },
                {
                    "server_id": "server-five",
                    "name": "Five",
                    "risk_tier": "medium",
                },
                {
                    "server_id": "server-one-axis",
                    "name": "One Axis",
                    "risk_tier": "low",
                },
            ],
        )

        score_rows = []
        for axis in CANONICAL_AXES:
            score_rows.append(
                {
                    "server_id": "server-one",
                    "axis_name": axis,
                    "label": "covered",
                    "p_top": 0.9,
                    "scored_at": "2024-01-01T00:00:00",
                }
            )

        for axis in CANONICAL_AXES[:5]:
            score_rows.append(
                {
                    "server_id": "server-five",
                    "axis_name": axis,
                    "label": "covered",
                    "p_top": 0.8,
                    "scored_at": "2024-01-02T00:00:00",
                }
            )

        score_rows.append(
            {
                "server_id": "server-one-axis",
                "axis_name": CANONICAL_AXES[0],
                "label": "covered",
                "p_top": 0.7,
                "scored_at": "2024-01-03T00:00:00",
            }
        )

        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, p_top, scored_at)
                VALUES
                    (:server_id, :axis_name, :label, :p_top, :scored_at)
                """
            ),
            score_rows,
        )
        session.commit()

    global SessionLocal
    production_session_factory = SessionLocal
    SessionLocal = test_session_factory

    try:
        response = get_axis_label_coverage()

        assert response.total_servers == 3
        assert response.fully_scored == 1
        assert response.partial == 2

        by_id = {server.server_id: server for server in response.servers}
        assert len(by_id["server-five"].missing_axes) == 2
        assert by_id["server-five"].present_count == 5
        assert len(by_id["server-one-axis"].missing_axes) == 6
        assert by_id["server-one-axis"].present_count == 1
        assert set(by_id["server-one"].present_axes) == set(CANONICAL_AXES)
        assert set(by_id["server-one"].missing_axes) == set()

        filtered = get_axis_label_coverage(server_id="server-five")
        assert filtered.total_servers == 1
        assert filtered.servers[0].server_id == "server-five"

        thresholded = get_axis_label_coverage(min_axes=6)
        assert thresholded.total_servers == 1
        assert thresholded.servers[0].server_id == "server-one"
    finally:
        SessionLocal = production_session_factory
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()