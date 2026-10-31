from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveSnapshot


def _membership_server_ids(membership: Any) -> list[str]:
    """Return server IDs from the supported snapshot membership shapes."""
    value = membership

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if isinstance(value, dict):
        for key in ("server_ids", "serverIds", "members", "servers"):
            if key in value:
                value = value[key]
                break
        else:
            value = list(value.keys())

    if not isinstance(value, (list, tuple, set)):
        return []

    server_ids: list[str] = []
    for item in value:
        if isinstance(item, dict):
            for key in ("server_id", "serverId", "id"):
                if item.get(key) is not None:
                    item = item[key]
                    break
            else:
                continue

        if item is None:
            continue

        server_id = str(item).strip()
        if server_id and server_id not in server_ids:
            server_ids.append(server_id)

    return server_ids


def _get_coverage(session: Session, perspective_id: str) -> dict[str, Any]:
    perspective = session.execute(
        text(
            """
            SELECT id, name, description, facet_filters, created_at
            FROM perspectives
            WHERE id = :perspective_id
            """
        ),
        {"perspective_id": perspective_id},
    ).mappings().first()

    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    snapshot = session.execute(
        text(
            """
            SELECT id, taken_at, membership
            FROM perspective_snapshots
            WHERE perspective_id = :perspective_id
            ORDER BY taken_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"perspective_id": perspective_id},
    ).mappings().first()

    server_ids = _membership_server_ids(snapshot["membership"] if snapshot else None)

    registry_rows: list[Any] = []
    if server_ids:
        statement = text(
            """
            SELECT server_id, name, risk_tier, trust_score
            FROM mcp_server_registry
            WHERE server_id IN :server_ids
            """
        ).bindparams(bindparam("server_ids", expanding=True))
        registry_rows = list(
            session.execute(statement, {"server_ids": server_ids}).mappings().all()
        )

    tier_histogram: dict[str, int] = {}
    for row in registry_rows:
        tier = row["risk_tier"]
        if tier is not None and str(tier).strip():
            tier = str(tier).strip()
            tier_histogram[tier] = tier_histogram.get(tier, 0) + 1

    eligible_rows = [
        row
        for row in registry_rows
        if row["risk_tier"] is not None and str(row["risk_tier"]).strip()
    ]
    eligible_rows.sort(
        key=lambda row: (
            row["trust_score"] is None,
            row["trust_score"] if row["trust_score"] is not None else 0,
            str(row["server_id"]),
        )
    )

    top_risk_members = [
        {
            "server_id": row["server_id"],
            "name": row["name"],
            "risk_tier": row["risk_tier"],
            "trust_score": row["trust_score"],
        }
        for row in eligible_rows[:10]
    ]

    return {
        "perspective_id": perspective["id"],
        "name": perspective["name"],
        "description": perspective["description"],
        "snapshot_taken_at": snapshot["taken_at"] if snapshot else None,
        "member_count": len(server_ids),
        "members_found_count": len(registry_rows),
        "missing_count": len(server_ids) - len(registry_rows),
        "tier_histogram": tier_histogram,
        "top_risk_members": top_risk_members,
    }


def get_coverage(
    perspective_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _get_coverage(session, perspective_id)


def get_perspective_coverage(
    perspective_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _get_coverage(session, perspective_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE perspectives (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                facet_filters TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                org_id TEXT,
                updated_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE perspective_snapshots (
                id TEXT PRIMARY KEY,
                perspective_id TEXT NOT NULL,
                taken_at TEXT NOT NULL,
                membership TEXT
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
            INSERT INTO perspectives
                (id, name, description, facet_filters, created_at, created_by)
            VALUES
                ('p-1', 'Example Perspective', 'An example', '{}',
                 '2024-01-01 00:00:00', 'u-1')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO perspective_snapshots
                (id, perspective_id, taken_at, membership)
            VALUES
                ('s-1', 'p-1', '2024-01-02 00:00:00',
                 '["server-1", "server-2", "absent-server"]')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry
                (server_id, name, risk_tier, trust_score)
            VALUES
                ('server-1', 'Server One', 'CAUTION_LIMITED', 0.8),
                ('server-2', 'Server Two', 'HIGH_RISK_ISOLATED', 0.2)
            """
        )

    db_session = Session(engine)
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: db_session

    @test_app.get("/api/perspectives/{perspective_id}/coverage")
    def coverage_endpoint(perspective_id: str, session: Session = Depends(get_session)):
        return get_coverage(perspective_id, session)

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/p-1/coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["member_count"] == 3
        assert payload["missing_count"] == 1
        assert payload["tier_histogram"]["CAUTION_LIMITED"] == 1

    print("PASS")