from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

import requests
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _query_write_service(sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"sql": sql, "params": dict(params or {})},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("rows", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _risk_register_rows() -> list[dict[str, Any]]:
    rows = _query_write_service(
        """
        SELECT server_id, computed_at
        FROM mcp_risk_register
        WHERE server_id IS NOT NULL
        """
    )
    return [dict(row) for row in rows]


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text_value[:10])
        except ValueError:
            return None


def get_coverage(session: Session = Depends(get_session)) -> dict[str, Any]:
    registry_rows = session.execute(
        text(
            """
            SELECT server_id, risk_tier
            FROM mcp_server_registry
            """
        )
    ).mappings().all()

    registry_by_id: dict[str, str | None] = {}
    for row in registry_rows:
        server_id = row.get("server_id")
        if server_id is None:
            continue
        registry_by_id[str(server_id)] = row.get("risk_tier")

    register_rows = _risk_register_rows()
    register_server_ids = {
        str(row["server_id"])
        for row in register_rows
        if row.get("server_id") is not None
    }

    servers_with_register = len(register_server_ids & set(registry_by_id))
    servers_total = len(registry_by_id)
    orphans = len(register_server_ids - set(registry_by_id))

    computed_dates = [
        computed_date
        for computed_date in (_as_date(row.get("computed_at")) for row in register_rows)
        if computed_date is not None
    ]

    by_tier: dict[str, int] = {}
    for server_id in register_server_ids & set(registry_by_id):
        tier = registry_by_id[server_id]
        tier_name = str(tier) if tier is not None else "unknown"
        by_tier[tier_name] = by_tier.get(tier_name, 0) + 1

    coverage_pct = (
        round((servers_with_register / servers_total) * 100, 2)
        if servers_total
        else 0.0
    )

    return {
        "servers_with_register": servers_with_register,
        "servers_total": servers_total,
        "coverage_pct": coverage_pct,
        "orphans": orphans,
        "oldest_computed_at": min(computed_dates).isoformat() if computed_dates else None,
        "newest_computed_at": max(computed_dates).isoformat() if computed_dates else None,
        "by_tier": by_tier,
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", risk_tier="high"),
                McpServerRegistry(server_id="server-2", risk_tier="medium"),
                McpServerRegistry(server_id="server-3", risk_tier="low"),
            ]
        )
        session.commit()

    original_risk_register_rows = _risk_register_rows
    _risk_register_rows = lambda: [
        {"server_id": "server-1", "computed_at": "2024-01-01T00:00:00Z"},
        {"server_id": "server-2", "computed_at": "2024-01-03T00:00:00Z"},
        {"server_id": "unknown-server", "computed_at": "2024-01-02T00:00:00Z"},
    ]

    that_app = FastAPI()

    @that_app.get("/api/risk-register/coverage")
    def coverage_endpoint(session: Session = Depends(get_session)):
        return get_coverage(session)

    that_app.dependency_overrides[get_session] = lambda: session_factory()
    try:
        response = TestClient(that_app).get("/api/risk-register/coverage")
        assert response.status_code == 200
        body = response.json()
        assert body["servers_with_register"] == 2
        assert body["servers_total"] == 3
        assert body["orphans"] == 1
        print("PASS")
    finally:
        _risk_register_rows = original_risk_register_rows
        that_app.dependency_overrides.clear()
        engine.dispose()