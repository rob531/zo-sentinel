from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


WRITE_SERVICE_QUERY_URL = "http://127.0.0.1:8772/query"

COMMUNITY_SIGNAL_SQL = """
SELECT score AS community_signal, evidence, scored_at AS timestamp
FROM mcp_signal_scores
WHERE server_id = :server_id
  AND signal_name = :signal_name
ORDER BY scored_at DESC
LIMIT 1
"""


def _query_mesh(server_id: str) -> dict[str, Any] | None:
    response = requests.post(
        WRITE_SERVICE_QUERY_URL,
        json={
            "query": COMMUNITY_SIGNAL_SQL,
            "params": {
                "server_id": server_id,
                "signal_name": "community_signal",
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _as_evidence(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _as_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def get_community_signal_logic(
    server_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    normalized_server_id = str(server_id)
    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == normalized_server_id)
        .first()
    )
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    try:
        row = _query_mesh(normalized_server_id)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve community signal",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Community signal not found for this server",
        )

    signal_value = row.get("community_signal", row.get("score"))
    if signal_value is None:
        raise HTTPException(status_code=502, detail="Community signal value missing")

    try:
        normalized_signal_value = float(signal_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Invalid community signal value") from exc

    return {
        "server_id": normalized_server_id,
        "signal_value": normalized_signal_value,
        "evidence": _as_evidence(row.get("evidence", row.get("evidence_blob"))),
        "timestamp": _as_timestamp(row.get("timestamp", row.get("scored_at"))),
    }


get_community_signal = get_community_signal_logic


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
    McpServerRegistry.__table__.create(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    test_server_id = "test_server_123"
    test_signal_value = 0.87

    with TestSessionLocal() as session:
        session.add(McpServerRegistry(server_id=test_server_id, name="Test Server"))
        session.commit()

    def get_test_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    original_query_mesh = _query_mesh

    def seeded_query_mesh(server_id: str) -> dict[str, Any] | None:
        if server_id != test_server_id:
            return None
        return {
            "community_signal": test_signal_value,
            "evidence": "test evidence blob",
            "timestamp": "2024-01-15T10:30:00+00:00",
        }

    _query_mesh = seeded_query_mesh

    test_app = FastAPI()

    @test_app.get("/api/signals/community")
    async def test_endpoint(
        server_id: str,
        session: Session = Depends(get_session),
    ):
        return await get_community_signal_logic(server_id, session)

    test_app.dependency_overrides[get_session] = get_test_session

    try:
        response = TestClient(test_app).get(
            "/api/signals/community",
            params={"server_id": test_server_id},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["signal_value"] == test_signal_value
        print("PASS")
    finally:
        _query_mesh = original_query_mesh
        McpServerRegistry.__table__.drop(engine)
        engine.dispose()
