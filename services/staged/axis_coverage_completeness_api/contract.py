from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class MissingAxisCount(BaseModel):
    axis_name: str
    missing: int


class LeastScoredServer(BaseModel):
    server_id: str
    name: str
    axes_scored: int


class AxisCoverageCompletenessResponse(BaseModel):
    total_servers: int
    fully_scored: int
    partially_scored: int
    unscored: int
    coverage_ratio: float
    missing_axis_counts: list[MissingAxisCount]
    least_scored_servers: list[LeastScoredServer]


router = APIRouter(prefix="/api")


def _coverage_query() -> tuple[str, dict[str, str]]:
    params = {f"axis_{index}": axis for index, axis in enumerate(AXIS_NAMES)}
    axis_placeholders = ", ".join(f":{name}" for name in params)

    conditional_counts = ",\n".join(
        (
            "COUNT(DISTINCT CASE "
            f"WHEN s.axis_name = :{name} THEN r.server_id "
            f"END) AS {name}_scored"
        )
        for name in params
    )

    query = f"""
        SELECT
            r.server_id AS server_id,
            r.name AS name,
            r.risk_tier AS risk_tier,
            r.last_scanned AS last_scanned,
            COUNT(DISTINCT s.axis_name) AS axes_scored,
            {conditional_counts}
        FROM mcp_server_registry AS r
        LEFT JOIN mcp_llm_axis_scores AS s
            ON s.server_id = r.server_id
            AND s.axis_name IN ({axis_placeholders})
        GROUP BY
            r.server_id,
            r.name,
            r.risk_tier,
            r.last_scanned
        ORDER BY
            axes_scored ASC,
            r.server_id ASC
    """
    return query, params


@router.get(
    "/axes/coverage-completeness",
    response_model=AxisCoverageCompletenessResponse,
)
async def get_axis_coverage_completeness(
    session: Session = Depends(get_session),
) -> AxisCoverageCompletenessResponse:
    query, params = _coverage_query()
    result = session.execute(text(query), params)
    if isawaitable(result):
        result = await result

    rows = result.mappings().all()
    total_servers = len(rows)
    fully_scored = 0
    partially_scored = 0
    unscored = 0
    least_scored_servers: list[LeastScoredServer] = []
    missing_axis_counts = [
        MissingAxisCount(axis_name=axis, missing=total_servers)
        for axis in AXIS_NAMES
    ]

    for row in rows:
        axes_scored = int(row["axes_scored"])

        if axes_scored == len(AXIS_NAMES):
            fully_scored += 1
        elif axes_scored == 0:
            unscored += 1
        else:
            partially_scored += 1

        for index, axis in enumerate(AXIS_NAMES):
            scored_servers = int(row[f"axis_{index}_scored"])
            missing_axis_counts[index].missing = total_servers - scored_servers

        least_scored_servers.append(
            LeastScoredServer(
                server_id=str(row["server_id"]),
                name=str(row["name"]),
                axes_scored=axes_scored,
            )
        )

    coverage_ratio = (
        fully_scored / total_servers if total_servers else 0.0
    )

    return AxisCoverageCompletenessResponse(
        total_servers=total_servers,
        fully_scored=fully_scored,
        partially_scored=partially_scored,
        unscored=unscored,
        coverage_ratio=coverage_ratio,
        missing_axis_counts=missing_axis_counts,
        least_scored_servers=least_scored_servers,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        risk_tier TEXT,
                        last_scanned TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE mcp_llm_axis_scores (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id TEXT NOT NULL,
                        axis_name TEXT NOT NULL,
                        p_top REAL,
                        scored_at TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, last_scanned)
                    VALUES
                        ('server-full', 'Fully Scored', 'high', '2026-01-01'),
                        ('server-partial', 'Partially Scored', 'medium', '2026-01-02'),
                        ('server-none', 'Unscored', 'low', '2026-01-03')
                    """
                )
            )

            score_id = 0
            for server_id, axes in (
                ("server-full", AXIS_NAMES),
                ("server-partial", AXIS_NAMES[:3]),
                ("server-none", ()),
            ):
                for axis in axes:
                    score_id += 1
                    connection.execute(
                        text(
                            """
                            INSERT INTO mcp_llm_axis_scores
                                (id, server_id, axis_name, p_top, scored_at)
                            VALUES
                                (:id, :server_id, :axis_name, :p_top, :scored_at)
                            """
                        ),
                        {
                            "id": score_id,
                            "server_id": server_id,
                            "axis_name": axis,
                            "p_top": 0.5,
                            "scored_at": "2026-01-03T00:00:00Z",
                        },
                    )

        def override_get_session() -> Any:
            session = test_session_factory()
            try:
                yield session
            finally:
                session.close()

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get("/api/axes/coverage-completeness")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["total_servers"] == 3
            assert payload["fully_scored"] == 1
            assert payload["partially_scored"] == 1
            assert payload["unscored"] == 1
            assert payload["coverage_ratio"] == 1 / 3
            assert len(payload["missing_axis_counts"]) == 7
            assert payload["least_scored_servers"][0]["server_id"] == "server-none"
            assert payload["least_scored_servers"][0]["axes_scored"] == 0
            assert payload["least_scored_servers"][-1]["server_id"] == "server-full"
            assert payload["least_scored_servers"][-1]["axes_scored"] == 7

        print("PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    _run_self_test()