from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

RISK_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisDelta(BaseModel):
    current: float | None
    previous: float | None
    delta: float | None
    direction: str | None


class ServerDelta(BaseModel):
    server_id: str
    name: str
    axes: dict[str, AxisDelta]
    overall_delta: float | None


class DeltaReportResponse(BaseModel):
    period_days: int
    servers: list[ServerDelta]


def get_delta_report(
    days: int,
    session: Session | None = None,
) -> DeltaReportResponse:
    """Compute axis-level score changes over N days for all servers."""
    if session is None:
        from app.db import get_session as gs
        from app.models import McpLlmAxisScore, McpServerRegistry

        with gs() as sess:
            return get_delta_report(days, sess)
        return None

    cutoff = datetime.utcnow() - timedelta(days=days)
    results: list[ServerDelta] = []

    servers_query = (
        "SELECT server_id, name FROM mcp_server_registry ORDER BY server_id"
    )
    servers = session.execute(text(servers_query)).fetchall()

    for server_row in servers:
        server_id = server_row[0]
        name = server_row[1]

        axes_data: dict[str, AxisDelta] = {}
        overall_deltas: list[float] = []

        for axis in RISK_AXES:
            latest_query = text("""
                SELECT p_top FROM mcp_llm_axis_scores
                WHERE server_id = :server_id
                  AND axis_name = :axis
                  AND scored_at >= :cutoff
                ORDER BY scored_at DESC
                LIMIT 1
            """)
            latest = session.execute(
                latest_query,
                {"server_id": server_id, "axis": axis, "cutoff": cutoff},
            ).scalar()

            prev_query = text("""
                SELECT p_top FROM mcp_llm_axis_scores
                WHERE server_id = :server_id
                  AND axis_name = :axis
                  AND scored_at < :cutoff
                ORDER BY scored_at DESC
                LIMIT 1
            """)
            prev = session.execute(
                prev_query,
                {"server_id": server_id, "axis": axis, "cutoff": cutoff},
            ).scalar()

            if latest is not None and prev is not None:
                delta = float(latest) - float(prev)
                direction = "up" if delta > 0 else "down" if delta < 0 else "stable"
                overall_deltas.append(abs(delta))
            else:
                delta = None
                direction = None

            axes_data[axis] = AxisDelta(
                current=float(latest) if latest is not None else None,
                previous=float(prev) if prev is not None else None,
                delta=round(delta, 6) if delta is not None else None,
                direction=direction,
            )

        overall_delta = round(sum(overall_deltas), 6) if overall_deltas else None

        results.append(
            ServerDelta(
                server_id=server_id,
                name=name,
                axes=axes_data,
                overall_delta=overall_delta,
            )
        )

    return DeltaReportResponse(period_days=days, servers=results)


@router.get("/api/scoring/delta", response_model=DeltaReportResponse)
def delta_endpoint(days: int = 7, session: Session = Depends(get_session)) -> Any:
    """GET /api/scoring/delta?days=N - Returns axis-level score changes over N days."""
    return get_delta_report(days, session)


if __name__ == "__main__":
    import json

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    session.execute(
        text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                meta TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER DEFAULT 0
            )
        """)
    )

    session.execute(
        text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL NOT NULL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                escalated INTEGER DEFAULT 0,
                escalated_to TEXT,
                scored_at TIMESTAMP NOT NULL
            )
        """)
    )

    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)

    servers = [
        ("srv_001", "Alpha Server"),
        ("srv_002", "Beta Server"),
        ("srv_003", "Gamma Server"),
    ]
    for sid, name in servers:
        session.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": sid, "name": name},
        )

    test_scores = [
        ("srv_001", "overall_risk", 0.75, 0.70),
        ("srv_001", "auth_strength", 0.60, 0.65),
        ("srv_002", "overall_risk", 0.50, 0.45),
        ("srv_002", "data_sensitivity", 0.80, 0.80),
        ("srv_003", "exploit_surface", 0.30, 0.40),
    ]

    for sid, axis, latest_p, prev_p in test_scores:
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, scored_at)
                VALUES (:sid, :axis, :p_top, :scored_at)
            """),
            {"sid": sid, "axis": axis, "p_top": latest_p, "scored_at": now},
        )
        session.execute(
            text("""
                INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, scored_at)
                VALUES (:sid, :axis, :p_top, :scored_at)
            """),
            {"sid": sid, "axis": axis, "p_top": prev_p, "scored_at": two_days_ago},
        )

    session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session

    import uvicorn
    from multiprocessing import Process

    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=18773, log_level="error")

    server_proc = Process(target=run_server)
    server_proc.start()

    import time
    time.sleep(1.5)

    try:
        import requests

        resp = requests.get("http://127.0.0.1:18773/api/scoring/delta?days=1", timeout=5)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        data = resp.json()
        assert "servers" in data
        assert "period_days" in data
        assert data["period_days"] == 1

        found_nonzero = False
        for server in data["servers"]:
            for axis, axis_data in server.get("axes", {}).items():
                if axis_data.get("delta") is not None and axis_data["delta"] != 0:
                    found_nonzero = True
                    break

        assert found_nonzero, "Expected at least one non-zero delta"

        print("PASS")
    finally:
        server_proc.terminate()
        server_proc.join()