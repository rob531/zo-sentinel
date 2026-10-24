from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_transitions(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")

    as_of = datetime.now(timezone.utc)
    cutoff = as_of - timedelta(days=days)

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        ).where(
            McpServerRegistry.last_assessed.is_not(None),
            McpServerRegistry.last_assessed >= cutoff,
        )
    ).all()

    counts: Counter[tuple[str, str]] = Counter()
    verdicts: set[str] = set()

    for server_id, verdict, risk_tier, last_assessed in rows:
        from_verdict = str(verdict) if verdict is not None else "unknown"
        to_verdict = str(risk_tier) if risk_tier is not None else "unknown"
        counts[(from_verdict, to_verdict)] += 1
        if verdict is not None:
            verdicts.add(str(verdict))

    servers = len(rows)
    transitions = [
        {
            "from_verdict": from_verdict,
            "to_verdict": to_verdict,
            "count": count,
            "pct": float(count / servers) if servers else 0.0,
        }
        for (from_verdict, to_verdict), count in sorted(counts.items())
    ]

    return {
        "days": days,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "transitions": transitions,
        "totals": {
            "servers": servers,
            "verdicts": len(verdicts),
        },
    }


def get_registry_verdict_transitions(
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_verdict_transitions(days=days, session=session)


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

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                verdict VARCHAR,
                risk_tier VARCHAR,
                last_assessed TIMESTAMP
            )
            """
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        values = [
            ("server-1", "trusted", "low", now),
            ("server-2", "trusted", "low", now),
            ("server-3", "restricted", "medium", now),
            ("server-4", "restricted", "medium", now),
            ("server-5", "blocked", "high", now),
            ("server-6", "trusted", "medium", now),
        ]
        for server_id, verdict, risk_tier, last_assessed in values:
            connection.exec_driver_sql(
                """
                INSERT INTO mcp_server_registry
                    (server_id, verdict, risk_tier, last_assessed)
                VALUES (?, ?, ?, ?)
                """,
                (server_id, verdict, risk_tier, last_assessed),
            )

    local_session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with local_session_factory() as session:
            yield session

    application = FastAPI()
    application.dependency_overrides[get_session] = override_get_session

    @application.get("/api/registry/verdict-transitions")
    def registry_verdict_transitions(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_verdict_transitions(days=days, session=session)

    response = TestClient(application).get(
        "/api/registry/verdict-transitions",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"days", "as_of", "transitions", "totals"}
    assert payload["transitions"]
    assert sum(item["count"] for item in payload["transitions"]) == payload["totals"]["servers"]
    assert all(
        isinstance(item["pct"], float) and 0.0 <= item["pct"] <= 1.0
        for item in payload["transitions"]
    )
    print("PASS")