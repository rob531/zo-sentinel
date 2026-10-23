from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_session

router = APIRouter(prefix="/api", tags=["risk"])


class AxisData(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class ServerData(BaseModel):
    server_id: str
    name: str
    axes: list[AxisData]
    overall_risk: float
    risk_tier: str


class ComparisonResponse(BaseModel):
    servers: list[ServerData]


@router.get("/servers/compare", response_model=ComparisonResponse)
def get_server_comparison(
    server_ids: str = Query(..., description="Comma-separated server IDs"),
    session: Session = Depends(get_session),
):
    ids = [s.strip() for s in server_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="server_ids required")
    return get_comparison(session, ids)


def get_comparison(session: Session, server_ids: list[str]) -> ComparisonResponse:
    placeholders = ",".join([f":id{i}" for i in range(len(server_ids))])
    params = {f"id{i}": sid for i, sid in enumerate(server_ids)}

    reg_query = text(f"""
        SELECT server_id, name, overall_risk, risk_tier
        FROM McpServerRegistry
        WHERE server_id IN ({placeholders})
    """)
    servers = session.execute(reg_query, params).fetchall()

    if not servers:
        return ComparisonResponse(servers=[])

    axes_query = text(f"""
        SELECT server_id, axis_name, label, p_top, p_critical, p_danger
        FROM McpLlmAxisScore
        WHERE server_id IN ({placeholders})
    """)
    axes_rows = session.execute(axes_query, params).fetchall()

    axes_map: dict[str, list[dict]] = {}
    for row in axes_rows:
        sid = row[0]
        if sid not in axes_map:
            axes_map[sid] = []
        axes_map[sid].append({
            "axis_name": row[1],
            "label": row[2],
            "p_top": row[3],
            "p_critical": row[4],
            "p_danger": row[5],
        })

    result = []
    for srv in servers:
        result.append(ServerData(
            server_id=srv[0],
            name=srv[1],
            axes=[AxisData(**a) for a in axes_map.get(srv[0], [])],
            overall_risk=srv[2] or 0.0,
            risk_tier=srv[3] or "UNKNOWN",
        ))
    return ComparisonResponse(servers=result)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                overall_risk REAL,
                risk_tier TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry VALUES
            ('srv1', 'Server One', 0.55, 'MEDIUM'),
            ('srv2', 'Server Two', 0.78, 'HIGH'),
            ('srv3', 'Server Three', 0.25, 'LOW')
        """))
        axis_scores = [
            ("srv1", "overall_risk", "Moderate", 0.20, 0.35, 0.45),
            ("srv1", "auth_strength", "Strong", 0.15, 0.25, 0.60),
            ("srv1", "capability_breadth", "Wide", 0.25, 0.30, 0.45),
            ("srv1", "data_sensitivity", "High", 0.10, 0.40, 0.50),
            ("srv1", "network_egress", "Moderate", 0.20, 0.35, 0.45),
            ("srv1", "maintainer_trust", "Trusted", 0.30, 0.20, 0.50),
            ("srv1", "exploit_surface", "Low", 0.35, 0.25, 0.40),
            ("srv2", "overall_risk", "Elevated", 0.10, 0.50, 0.40),
            ("srv2", "auth_strength", "Moderate", 0.20, 0.35, 0.45),
            ("srv2", "capability_breadth", "Narrow", 0.40, 0.25, 0.35),
            ("srv2", "data_sensitivity", "Critical", 0.05, 0.60, 0.35),
            ("srv2", "network_egress", "Low", 0.50, 0.20, 0.30),
            ("srv2", "maintainer_trust", "Unverified", 0.15, 0.45, 0.40),
            ("srv2", "exploit_surface", "High", 0.10, 0.55, 0.35),
            ("srv3", "overall_risk", "Low", 0.45, 0.15, 0.40),
            ("srv3", "auth_strength", "Very Strong", 0.35, 0.15, 0.50),
            ("srv3", "capability_breadth", "Very Wide", 0.15, 0.45, 0.40),
            ("srv3", "data_sensitivity", "Low", 0.55, 0.10, 0.35),
            ("srv3", "network_egress", "Very Low", 0.60, 0.10, 0.30),
            ("srv3", "maintainer_trust", "Highly Trusted", 0.45, 0.10, 0.45),
            ("srv3", "exploit_surface", "Very Low", 0.50, 0.10, 0.40),
        ]
        for srv_id, axis, label, p_top, p_critical, p_danger in axis_scores:
            conn.execute(
                text("INSERT INTO McpLlmAxisScore (server_id,axis_name,label,p_top,p_critical,p_danger) VALUES (:s,:a,:l,:pt,:pc,:pd)"),
                {"s": srv_id, "a": axis, "l": label, "pt": p_top, "pc": p_critical, "pd": p_danger},
            )

    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override():
        return TestingSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/servers/compare?server_ids=srv1,srv2", timeout=10)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert "servers" in data
    assert len(data["servers"]) == 2

    for server in data["servers"]:
        assert "server_id" in server
        assert "name" in server
        assert "axes" in server
        assert len(server["axes"]) == 7

        axis_names = [a["axis_name"] for a in server["axes"]]
        for expected in ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
            assert expected in axis_names, f"Missing axis: {expected}"

        assert "overall_risk" in server
        assert "risk_tier" in server

    print("PASS")
    sys.exit(0)