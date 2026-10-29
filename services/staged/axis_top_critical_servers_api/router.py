from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_top_critical_servers


router = APIRouter(prefix="/api")


class TopCriticalServerResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    registry_source: Optional[str] = None
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_critical: float
    p_danger: float
    escalated: bool = False
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime] = None


class TopCriticalServersResponse(BaseModel):
    axis: str
    limit: int
    servers: List[TopCriticalServerResponse]


@router.get(
    "/axis/top-critical-servers",
    response_model=TopCriticalServersResponse,
)
def top_critical_servers(
    axis: str = Query("exploit_surface"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    servers = get_top_critical_servers(db, axis, limit)
    return {
        "axis": axis,
        "limit": limit,
        "servers": servers,
    }


def _self_test() -> None:
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
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    registry_source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    server_id TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated BOOLEAN,
                    escalated_to TEXT,
                    scored_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, risk_tier, registry_source)
                VALUES
                    ('srv-low', 'Low Risk Server', 'approved', 'low', 'registry'),
                    ('srv-high', 'High Risk Server', 'blocked', 'critical', 'registry'),
                    ('srv-medium', 'Medium Risk Server', 'review', 'medium', 'registry')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top,
                     p_critical, p_danger, escalated, escalated_to, scored_at)
                VALUES
                    ('srv-low', 'exploit_surface', 'low', 0, 0.1, 0.1, 0.2, 0, NULL, '2024-01-01T00:00:00'),
                    ('srv-high', 'exploit_surface', 'critical', 3, 0.9, 0.9, 0.8, 1, 'security_review', '2024-01-01T00:00:00'),
                    ('srv-medium', 'exploit_surface', 'medium', 2, 0.4, 0.4, 0.3, 0, NULL, '2024-01-01T00:00:00')
                """
            )
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/axis/top-critical-servers",
            params={"axis": "exploit_surface", "limit": 20},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["servers"]) == 3
        assert payload["servers"][0]["server_id"] == "srv-high"
        assert payload["servers"][0]["escalated_to"] == "security_review"

    print("PASS")


if __name__ == "__main__":
    _self_test()