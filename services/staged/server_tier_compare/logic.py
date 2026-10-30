from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_COUNT = 7


def _normalize_server_ids(server_ids: Iterable[str]) -> list[str]:
    normalized = [str(server_id).strip() for server_id in server_ids]
    normalized = [server_id for server_id in normalized if server_id]

    if not 2 <= len(normalized) <= 4:
        raise ValueError("server_ids must contain between 2 and 4 server identifiers")
    if len(set(normalized)) != len(normalized):
        raise ValueError("server_ids must not contain duplicates")
    return normalized


def _latest_scores(session: Any, server_ids: list[str]) -> list[Any]:
    newest_first = case(
        (McpLlmAxisScore.scored_at.is_(None), 1),
        else_=0,
    )

    ranked = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.id,
            func.row_number()
            .over(
                partition_by=(
                    McpLlmAxisScore.server_id,
                    McpLlmAxisScore.axis_name,
                ),
                order_by=(
                    newest_first.asc(),
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("latest_rank"),
        )
        .where(McpLlmAxisScore.server_id.in_(server_ids))
        .subquery()
    )

    result = session.execute(
        select(ranked).where(ranked.c.latest_rank == 1)
    )
    return result.mappings().all()


def _registry_servers(session: Any, server_ids: list[str]) -> dict[str, Any]:
    result = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
        ).where(McpServerRegistry.server_id.in_(server_ids))
    )
    return {row["server_id"]: row for row in result.mappings().all()}


def _axis_order(latest_rows: list[Any], server_ids: list[str]) -> list[str]:
    coverage: dict[str, int] = {}
    server_sets: dict[str, set[str]] = {server_id: set() for server_id in server_ids}

    for row in latest_rows:
        axis_name = row["axis_name"]
        if axis_name is None:
            continue
        coverage.setdefault(axis_name, 0)
        server_sets.setdefault(row["server_id"], set()).add(axis_name)

    for server_id, axis_names in server_sets.items():
        for axis_name in axis_names:
            coverage[axis_name] += 1

    return sorted(
        coverage,
        key=lambda axis_name: (-coverage[axis_name], axis_name),
    )[:AXIS_COUNT]


def compare_tiers(
    server_ids: Any = None,
    session: Any = None,
    *,
    db: Any = None,
) -> dict[str, Any]:
    if session is None:
        session = db

    if hasattr(server_ids, "execute"):
        server_ids, session = session, server_ids

    if server_ids is None or session is None:
        raise TypeError("server_ids and session are required")

    if isinstance(server_ids, str):
        server_ids = server_ids.split(",")

    normalized_ids = _normalize_server_ids(server_ids)
    latest_rows = _latest_scores(session, normalized_ids)
    registry = _registry_servers(session, normalized_ids)
    axis_order = _axis_order(latest_rows, normalized_ids)

    rows_by_server: dict[str, dict[str, Any]] = {
        server_id: {} for server_id in normalized_ids
    }
    for row in latest_rows:
        axis_name = row["axis_name"]
        if axis_name in axis_order:
            rows_by_server.setdefault(row["server_id"], {})[axis_name] = row

    servers: list[dict[str, Any]] = []
    riskiest_axis_per_server: dict[str, str] = {}

    for server_id in normalized_ids:
        registry_row = registry.get(server_id)
        server_axes: dict[str, dict[str, Any]] = {}
        numeric_scores: list[tuple[float, str]] = []

        for axis_name in axis_order:
            score = rows_by_server.get(server_id, {}).get(axis_name)
            if score is None:
                continue

            p_top = score["p_top"]
            p_top_value = float(p_top) if p_top is not None else None
            server_axes[axis_name] = {
                "label": score["label"],
                "p_top": p_top_value,
                "escalated": bool(score["escalated"]),
            }
            if p_top_value is not None:
                numeric_scores.append((p_top_value, axis_name))

        if numeric_scores:
            riskiest_axis_per_server[server_id] = max(
                numeric_scores,
                key=lambda item: (item[0], -axis_order.index(item[1])),
            )[1]

        trust_score = registry_row["trust_score"] if registry_row else None
        servers.append(
            {
                "server_id": server_id,
                "name": (
                    registry_row["name"]
                    if registry_row and registry_row["name"] is not None
                    else server_id
                ),
                "risk_tier": (
                    registry_row["risk_tier"]
                    if registry_row
                    else None
                ),
                "trust_score": (
                    float(trust_score) if trust_score is not None else None
                ),
                "axes": server_axes,
            }
        )

    return {
        "servers": servers,
        "axis_order": axis_order,
        "riskiest_axis_per_server": riskiest_axis_per_server,
    }


def compare_server_tiers(
    server_ids: Any = None,
    session: Any = None,
    *,
    db: Any = None,
) -> dict[str, Any]:
    return compare_tiers(server_ids, session, db=db)


def get_server_tier_comparison(
    server_ids: Any = None,
    session: Any = None,
    *,
    db: Any = None,
) -> dict[str, Any]:
    return compare_tiers(server_ids, session, db=db)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT,
                p_top REAL,
                escalated INTEGER,
                scored_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                risk_tier TEXT,
                trust_score REAL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry
                (server_id, name, risk_tier, trust_score)
            VALUES
                ('server-a', 'Server A', 'high', 0.25),
                ('server-b', 'Server B', 'medium', 0.75)
            """
        )

        axes = [f"axis_{index}" for index in range(AXIS_COUNT)]
        for server_index, server_id in enumerate(("server-a", "server-b")):
            for axis_index, axis_name in enumerate(axes):
                p_top = (axis_index + 1) / 10
                connection.exec_driver_sql(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (id, server_id, axis_name, label, p_top, escalated, scored_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        server_index * 100 + axis_index + 1,
                        server_id,
                        axis_name,
                        "danger" if axis_index == AXIS_COUNT - 1 else "normal",
                        p_top,
                        1 if axis_index == AXIS_COUNT - 1 else 0,
                        "2025-01-01T00:00:00",
                    ),
                )

        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores
                (id, server_id, axis_name, label, p_top, escalated, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1000,
                "server-a",
                axes[-1],
                "stale",
                0.99,
                0,
                "2024-01-01T00:00:00",
            ),
        )

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/compare/tiers")
    def compare_route(server_ids: str, db=Depends(get_session)):
        return compare_tiers(server_ids.split(","), db)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/compare/tiers",
            params={"server_ids": "server-a,server-b"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["servers"]) == 2
        assert {server["server_id"] for server in payload["servers"]} == {
            "server-a",
            "server-b",
        }
        assert all(len(server["axes"]) == AXIS_COUNT for server in payload["servers"])
        assert len(payload["axis_order"]) == AXIS_COUNT
        assert payload["riskiest_axis_per_server"]["server-a"] == axes[-1]
        assert payload["riskiest_axis_per_server"]["server-b"] == axes[-1]

    print("PASS")


if __name__ == "__main__":
    _self_test()