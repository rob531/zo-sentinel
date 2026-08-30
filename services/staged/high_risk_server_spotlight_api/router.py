# services/staged/high_risk_server_spotlight_api/router.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from typing import Annotated

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class ServerRiskProfile(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    overall_p_top: float
    axes: list[AxisScore]


class HighRiskServersResponse(BaseModel):
    servers: list[ServerRiskProfile]


@router.get("/api/servers/high-risk", response_model=HighRiskServersResponse)
def get_high_risk_servers(
    n: Annotated[int, {"description": "Number of top risk servers to return"}] = 20,
    session=Depends(get_session),
) -> HighRiskServersResponse:
    query = text("""
        WITH ranked_scores AS (
            SELECT
                s.server_id,
                s.axis_name,
                s.label,
                s.p_top,
                s.p_critical,
                s.p_danger,
                s.scored_at,
                ROW_NUMBER() OVER (
                    PARTITION BY s.server_id, s.axis_name
                    ORDER BY s.scored_at DESC
                ) AS rn
            FROM McpLlmAxisScore s
            INNER JOIN McpServerRegistry r ON s.server_id = r.server_id
            WHERE r.risk_tier IN ('HIGH_RISK_ISOLATED', 'CAUTION_LIMITED', 'KNOWN_THREAT')
        ),
        server_totals AS (
            SELECT
                rs.server_id,
                AVG(rs.p_top) AS overall_p_top
            FROM ranked_scores rs
            WHERE rs.rn = 1
            GROUP BY rs.server_id
        )
        SELECT
            r.server_id,
            r.name,
            r.risk_tier,
            st.overall_p_top,
            rs.axis_name,
            rs.label,
            rs.p_top,
            rs.p_critical,
            rs.p_danger
        FROM ranked_scores rs
        INNER JOIN McpServerRegistry r ON rs.server_id = r.server_id
        INNER JOIN server_totals st ON rs.server_id = st.server_id
        WHERE rs.rn = 1
        ORDER BY st.overall_p_top DESC, r.server_id, rs.axis_name
    """)
    
    result = session.execute(query)
    rows = result.fetchall()
    
    servers_dict: dict = {}
    for row in rows:
        server_id = row.server_id
        if server_id not in servers_dict:
            servers_dict[server_id] = {
                "server_id": server_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
                "overall_p_top": row.overall_p_top,
                "axes": [],
            }
        servers_dict[server_id]["axes"].append({
            "axis_name": row.axis_name,
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
        })
    
    sorted_servers = sorted(servers_dict.values(), key=lambda x: x["overall_p_top"], reverse=True)
    top_servers = sorted_servers[:n]
    
    servers = [ServerRiskProfile(**s) for s in top_servers]
    return HighRiskServersResponse(servers=servers)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    import datetime
    
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
                risk_tier TEXT NOT NULL,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                label TEXT NOT NULL,
                label_index INTEGER,
                probs TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                model_version TEXT,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                adapter_sha256 TEXT,
                scored_at TEXT
            )
        """))
    
    SessionLocal = sessionmaker(bind=engine)
    
    risk_tiers = [
        ("SRV-HIGH-001", "high-risk-server-1", "HIGH_RISK_ISOLATED"),
        ("SRV-HIGH-002", "high-risk-server-2", "CAUTION_LIMITED"),
        ("SRV-HIGH-003", "threat-server-1", "KNOWN_THREAT"),
        ("SRV-TRUST-001", "trusted-server-1", "TRUSTED"),
        ("SRV-TRUST-002", "trusted-server-2", "TRUSTED"),
    ]
    
    axis_names = [
        "data_exfiltration",
        "supply_chain",
        "prompt_injection",
        "model_manipulation",
        "lateral_movement",
        "persistence",
        "reconnaissance",
    ]
    
    with SessionLocal() as db:
        for server_id, name, risk_tier in risk_tiers:
            db.execute(
                text("INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES (:s, :n, :r)"),
                {"s": server_id, "n": name, "r": risk_tier}
            )
        
        base_time = datetime.datetime(2024, 1, 1, 12, 0, 0)
        for server_id, name, risk_tier in risk_tiers:
            for i, axis in enumerate(axis_names):
                scored_at = (base_time + datetime.timedelta(minutes=i)).isoformat()
                p_top = 0.3 + (i * 0.1)
                db.execute(
                    text("""
                        INSERT INTO McpLlmAxisScore 
                        (server_id, axis_name, label, p_top, p_critical, p_danger, scored_at)
                        VALUES (:s, :a, :l, :pt, :pc, :pd, :t)
                    """),
                    {
                        "s": server_id,
                        "a": axis,
                        "l": f"Label_{i}",
                        "pt": p_top,
                        "pc": p_top * 0.5,
                        "pd": p_top * 0.3,
                        "t": scored_at,
                    }
                )
        db.commit()
    
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/servers/high-risk")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    servers = data.get("servers", [])
    assert len(servers) == 3, f"Expected 3 high-risk servers, got {len(servers)}"
    
    for server in servers:
        assert len(server["axes"]) == 7, f"Expected 7 axes per server, got {len(server['axes'])}"
    
    print("PASS")