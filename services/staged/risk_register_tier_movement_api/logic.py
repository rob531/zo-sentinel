from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_tier_movement(session: Session = Depends(get_session)) -> dict[str, Any]:
    result = session.execute(
        text(
            """
            SELECT *
            FROM mcp_risk_register
            ORDER BY computed_at ASC, server_id ASC
            """
        )
    )
    rows = [dict(row) for row in result.mappings().all()]

    if not rows:
        return {
            "window": {"from": None, "to": None},
            "transitions": [],
            "servers_changed": 0,
            "servers_tracked": 0,
            "by_server": [],
        }

    rows_by_server: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        server_id = row.get("server_id")
        if server_id is not None:
            rows_by_server.setdefault(server_id, []).append(row)

    transition_counts: dict[tuple[Any, Any], int] = {}
    changed_servers: list[dict[str, Any]] = []

    for server_id, server_rows in rows_by_server.items():
        tiers = [row.get("risk_tier") for row in server_rows]
        present_tiers = [tier for tier in tiers if tier is not None]
        server_changed = False

        for previous_tier, next_tier in zip(tiers, tiers[1:]):
            if previous_tier is None or next_tier is None or previous_tier == next_tier:
                continue
            transition_counts[(previous_tier, next_tier)] = (
                transition_counts.get((previous_tier, next_tier), 0) + 1
            )
            server_changed = True

        if server_changed and present_tiers:
            changed_servers.append(
                {
                    "server_id": server_id,
                    "from_tier": present_tiers[0],
                    "to_tier": present_tiers[-1],
                }
            )

    transitions = [
        {
            "from_tier": from_tier,
            "to_tier": to_tier,
            "count": count,
        }
        for (from_tier, to_tier), count in sorted(
            transition_counts.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]

    return {
        "window": {
            "from": rows[0].get("computed_at"),
            "to": rows[-1].get("computed_at"),
        },
        "transitions": transitions,
        "servers_changed": len(changed_servers),
        "servers_tracked": len(rows_by_server),
        "by_server": changed_servers,
    }


if __name__ == "__main__":
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
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    computed_at TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    risk_score REAL,
                    risk_tier TEXT,
                    verdict TEXT,
                    reason TEXT,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register (
                    computed_at,
                    server_id,
                    risk_score,
                    risk_tier,
                    verdict,
                    reason,
                    meta
                ) VALUES (
                    :computed_at,
                    :server_id,
                    :risk_score,
                    :risk_tier,
                    :verdict,
                    :reason,
                    :meta
                )
                """
            ),
            [
                {
                    "computed_at": "2025-01-01T00:00:00Z",
                    "server_id": "server-a",
                    "risk_score": 20.0,
                    "risk_tier": "LOW",
                    "verdict": "allow",
                    "reason": "initial assessment",
                    "meta": "{}",
                },
                {
                    "computed_at": "2025-01-01T01:00:00Z",
                    "server_id": "server-b",
                    "risk_score": 50.0,
                    "risk_tier": "MEDIUM",
                    "verdict": "review",
                    "reason": "initial assessment",
                    "meta": "{}",
                },
                {
                    "computed_at": "2025-01-01T02:00:00Z",
                    "server_id": "server-a",
                    "risk_score": 60.0,
                    "risk_tier": "MEDIUM",
                    "verdict": "review",
                    "reason": "risk increased",
                    "meta": "{}",
                },
                {
                    "computed_at": "2025-01-01T03:00:00Z",
                    "server_id": "server-b",
                    "risk_score": 80.0,
                    "risk_tier": "HIGH",
                    "verdict": "block",
                    "reason": "risk increased",
                    "meta": "{}",
                },
            ],
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/risk/register/tier-movement")
    def tier_movement(payload: dict[str, Any] = Depends(get_tier_movement)):
        return payload

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/risk/register/tier-movement")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["servers_tracked"] == 2, payload
        assert payload["servers_changed"] == 2, payload
        assert sum(item["count"] for item in payload["transitions"]) == 2, payload

    engine.dispose()
    print("PASS")