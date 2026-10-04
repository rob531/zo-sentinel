from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy import text
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk"])


class AxisDelta(BaseModel):
    axis_name: str
    p_top: float
    delta: float


class ServerAxisDelta(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    deltas: List[AxisDelta]


class RiskAxisDeltaResponse(BaseModel):
    days: int
    servers: List[ServerAxisDelta]


def get_risk_axis_delta(days: int, session) -> RiskAxisDeltaResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = text("""
        SELECT 
            s.server_id,
            s.name,
            s.risk_tier,
            a.axis_name,
            a.p_top,
            a.p_critical,
            a.p_danger
        FROM mcp_llm_axis_scores a
        JOIN mcp_server_registry s ON a.server_id = s.server_id
        WHERE a.scored_at >= :cutoff
        ORDER BY s.server_id, a.axis_name
    """)
    result = session.execute(query, {"cutoff": cutoff})
    rows = result.fetchall()
    
    server_map = {}
    for row in rows:
        sid = row.server_id
        if sid not in server_map:
            server_map[sid] = {"server_id": sid, "name": row.name, "risk_tier": row.risk_tier, "deltas": []}
        server_map[sid]["deltas"].append({
            "axis_name": row.axis_name,
            "p_top": row.p_top,
            "delta": row.p_critical + row.p_danger
        })
    
    return RiskAxisDeltaResponse(
        days=days,
        servers=[ServerAxisDelta(**v) for v in server_map.values()]
    )


@router.get("/risk/axis-delta", response_model=RiskAxisDeltaResponse)
def get_axis_delta(days: int = 7, session=Depends(get_session)):
    return get_risk_axis_delta(days, session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    session = Session()
    
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            url TEXT,
            description TEXT,
            trust_score REAL,
            confidence REAL,
            verdict TEXT,
            verdict_reasoning TEXT,
            registry_source TEXT,
            first_seen TEXT,
            last_seen TEXT,
            last_scanned TEXT,
            last_assessed TEXT,
            scan_count INTEGER,
            meta TEXT
        )
    """))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL,
            axis_name TEXT NOT NULL,
            p_top REAL NOT NULL,
            p_critical REAL NOT NULL,
            p_danger REAL NOT NULL,
            p_medium REAL,
            p_low REAL,
            probs TEXT,
            label TEXT,
            label_index INTEGER,
            model_version TEXT,
            decision_rule_version TEXT,
            escalated INTEGER,
            escalated_to TEXT,
            adapter_sha256 TEXT,
            scored_at TEXT NOT NULL
        )
    """))
    session.commit()
    
    servers = [
        ("srv1", "Alpha", "high"),
        ("srv2", "Beta", "medium"),
        ("srv3", "Gamma", "low"),
        ("srv4", "Delta", "high"),
    ]
    for sid, name, tier in servers:
        session.execute(text("INSERT INTO mcp_server_registry (server_id, name, risk_tier) VALUES (:s, :n, :t)"),
                        {"s": sid, "n": name, "t": tier})
    
    scored_at = datetime.utcnow().isoformat()
    axes = [
        ("srv1", "overall_risk", 0.3, 0.5),
        ("srv1", "security", 0.4, 0.6),
        ("srv1", "reliability", 0.2, 0.3),
        ("srv2", "overall_risk", 0.5, 0.4),
        ("srv2", "security", 0.6, 0.5),
        ("srv3", "overall_risk", 0.1, 0.1),
        ("srv3", "reliability", 0.15, 0.2),
        ("srv4", "overall_risk", 0.7, 0.2),
        ("srv4", "security", 0.8, 0.3),
    ]
    for sid, axis, p_top, p_crit in axes:
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, p_danger, scored_at)
            VALUES (:s, :a, :pt, :pc, 0.1, :sa)
        """), {"s": sid, "a": axis, "pt": p_top, "pc": p_crit, "sa": scored_at})
    session.commit()
    
    app = FastAPI()
    app.include_router(router)
    
    from app.main import app as real_app
    app.dependency_overrides[get_session] = lambda: session
    
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app)
    resp = client.get("/api/risk/axis-delta?days=7")
    
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "servers" in data
    assert len(data["servers"]) > 0
    for srv in data["servers"]:
        assert "deltas" in srv
        assert len(srv["deltas"]) > 0
    
    print("PASS")