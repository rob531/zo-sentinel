"""server_risk_timeline_api contract -- get risk timeline for a server."""
from datetime import datetime, timezone
from typing import Sequence

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_timeline_api"])


class TimelinePoint(BaseModel):
    scored_at: datetime
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    risk_tier: str | None


class ServerTimelineResponse(BaseModel):
    server_id: str
    server_name: str
    series: list[TimelinePoint]


def compute_risk_timeline(
    server_id: str,
    session: Session,
) -> ServerTimelineResponse:
    """Return the risk timeline for a server.

    Reads McpLlmAxisScore joined to McpServerRegistry ordered by scored_at ASC.
    Returns {server_id, server_name, series: [{scored_at, axis_name, label, p_top, p_critical, p_danger, risk_tier}]}.
    """
    # Postgres-portable parameterized query
    server_row = session.execute(
        text("SELECT name FROM McpServerRegistry WHERE server_id = :server_id"),
        {"server_id": server_id},
    ).fetchone()
    server_name = server_row[0] if server_row else ""

    rows = session.execute(
        text(
            """
            SELECT
                s.scored_at,
                s.axis_name,
                s.label,
                s.p_top,
                s.p_critical,
                s.p_danger,
                r.risk_tier
            FROM McpLlmAxisScore s
            JOIN McpServerRegistry r ON r.server_id = s.server_id
            WHERE s.server_id = :server_id
            ORDER BY s.scored_at ASC
            """
        ),
        {"server_id": server_id},
    ).fetchall()

    series = [
        TimelinePoint(
            scored_at=row[0],
            axis_name=row[1],
            label=row[2],
            p_top=row[3],
            p_critical=row[4],
            p_danger=row[5],
            risk_tier=row[6],
        )
        for row in rows
    ]

    return ServerTimelineResponse(
        server_id=server_id,
        server_name=server_name,
        series=series,
    )


@router.get("/servers/{server_id}/timeline", response_model=ServerTimelineResponse)
def get_risk_timeline_for_server(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerTimelineResponse:
    """Get the risk timeline for a specific server."""
    return compute_risk_timeline(server_id, session)


def _create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _run_self_test() -> bool:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create schema
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE McpServerRegistry (server_id TEXT PRIMARY KEY, name TEXT, risk_tier TEXT)"))
        conn.execute(text("CREATE TABLE McpLlmAxisScore (id INTEGER PRIMARY KEY, server_id TEXT, scored_at TEXT, axis_name TEXT, label TEXT, p_top REAL, p_critical REAL, p_danger REAL)"))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        return SessionLocal()

    # Seed data: 2 servers, 3 scored_at timestamps each
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES (:s1, 'Server Alpha', 'medium')"), {"s1": "srv-001"})
        conn.execute(text("INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES (:s2, 'Server Beta', 'high')"), {"s2": "srv-002"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'top', 0.9, 0.05, 0.05)"), {"sid": "srv-001", "t": "2024-01-01T00:00:00Z"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'danger', 0.1, 0.6, 0.3)"), {"sid": "srv-001", "t": "2024-01-02T00:00:00Z"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'critical', 0.2, 0.7, 0.1)"), {"sid": "srv-001", "t": "2024-01-03T00:00:00Z"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'top', 0.85, 0.1, 0.05)"), {"sid": "srv-002", "t": "2024-01-01T00:00:00Z"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'top', 0.7, 0.2, 0.1)"), {"sid": "srv-002", "t": "2024-01-02T00:00:00Z"})
        conn.execute(text("INSERT INTO McpLlmAxisScore (server_id, scored_at, axis_name, label, p_top, p_critical, p_danger) VALUES (:sid, :t, 'security', 'danger', 0.3, 0.3, 0.4)"), {"sid": "srv-002", "t": "2024-01-03T00:00:00Z"})
        conn.commit()

    app = _create_app()
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Test server 1
    resp = client.get("/api/servers/srv-001/timeline")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code}")
        return False
    data = resp.json()
    if len(data["series"]) < 3:
        print(f"FAIL: series length {len(data['series'])} < 3")
        return False
    for pt in data["series"]:
        if not (0 <= pt["p_top"] <= 1):
            print(f"FAIL: p_top {pt['p_top']} out of [0,1]")
            return False

    # Test server 2
    resp2 = client.get("/api/servers/srv-002/timeline")
    if resp2.status_code != 200:
        print(f"FAIL: status {resp2.status_code}")
        return False
    data2 = resp2.json()
    if len(data2["series"]) < 3:
        print(f"FAIL: series length {len(data2['series'])} < 3")
        return False

    print("PASS")
    return True


if __name__ == "__main__":
    import sys
    success = _run_self_test()
    sys.exit(0 if success else 1)