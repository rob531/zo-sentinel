from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api/attestations", tags=["attestations"])

_REGISTRY_TABLE = McpServerRegistry.__tablename__
_AXIS_SCORE_TABLE = McpLlmAxisScore.__tablename__


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def get_attestation_coverage_summary(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    sql = text(
        f"""
        WITH latest_attestation AS (
            SELECT
                server_id,
                MAX(created_at) AS last_attested_at
            FROM mcp_attestations
            GROUP BY server_id
        ),
        latest_axis_score AS (
            SELECT
                server_id,
                MAX(scored_at) AS last_scored_at
            FROM {_AXIS_SCORE_TABLE}
            GROUP BY server_id
        )
        SELECT
            r.server_id AS server_id,
            r.name AS name,
            s.last_scored_at AS last_scored_at,
            a.last_attested_at AS last_attested_at,
            CASE
                WHEN a.server_id IS NULL THEN 1
                ELSE 0
            END AS unattested,
            CASE
                WHEN a.server_id IS NOT NULL
                     AND a.last_attested_at < :cutoff
                THEN 1
                ELSE 0
            END AS stale
        FROM {_REGISTRY_TABLE} AS r
        LEFT JOIN latest_attestation AS a
            ON a.server_id = r.server_id
        LEFT JOIN latest_axis_score AS s
            ON s.server_id = r.server_id
        ORDER BY r.server_id
        """
    )

    rows = session.execute(sql, {"cutoff": cutoff}).mappings().all()

    total_servers = len(rows)
    attested_servers = sum(
        1 for row in rows if row["last_attested_at"] is not None
    )
    stale_attestations = sum(1 for row in rows if row["stale"])
    coverage_pct = (
        round((attested_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    unattested_sample = [
        {
            "server_id": row["server_id"],
            "name": row["name"],
            "last_scored_at": _iso8601(row["last_scored_at"]),
        }
        for row in rows
        if row["unattested"]
    ]

    return {
        "total_servers": total_servers,
        "attested_servers": attested_servers,
        "coverage_pct": coverage_pct,
        "stale_attestations": stale_attestations,
        "unattested_sample": unattested_sample,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/coverage")
def get_coverage(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_attestation_coverage_summary(session=session)


get_coverage_summary = get_attestation_coverage_summary


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    scored_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
                """
            ),
            [
                {"server_id": "server-fresh", "name": "Fresh Server"},
                {"server_id": "server-unattested", "name": "Unattested Server"},
                {"server_id": "server-stale", "name": "Stale Server"},
            ],
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, scored_at)
                VALUES
                    (1, 'server-fresh', 'security', :now),
                    (2, 'server-unattested', 'security', :now),
                    (3, 'server-stale', 'security', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations (id, server_id, created_at)
                VALUES
                    (1, 'server-fresh', :fresh),
                    (2, 'server-stale', :stale)
                """
            ),
            {
                "fresh": now - timedelta(days=1),
                "stale": now - timedelta(days=31),
            },
        )

    testing_session = TestingSession()
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: testing_session
    test_app.include_router(router)

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/attestations/coverage")
            assert response.status_code == 200
            payload = response.json()
            assert payload["coverage_pct"] == 66.67
            assert payload["stale_attestations"] == 1
            assert any(
                item["server_id"] == "server-unattested"
                for item in payload["unattested_sample"]
            )
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")