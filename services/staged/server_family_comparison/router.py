from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_family_comparison"])


class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: dict


class PeerServer(BaseModel):
    server_id: int
    name: str
    risk_tier: str


class AxisComparisonResponse(BaseModel):
    server_id: int
    axes: dict[str, AxisScore]
    peers: list[PeerServer]


@router.get("/servers/{server_id}/axis-comparison", response_model=AxisComparisonResponse)
def get_axis_comparison(
    server_id: int,
    session: Session = Depends(get_session),
) -> AxisComparisonResponse:
    # Get target server's domain/registry_source for peer matching
    target_query = text("""
        SELECT id, name, domain, registry_source, risk_tier
        FROM McpServerRegistry
        WHERE id = :server_id
    """)
    target_result = session.execute(target_query, {"server_id": server_id}).fetchone()
    
    if not target_result:
        return AxisComparisonResponse(server_id=server_id, axes={}, peers=[])
    
    target_domain = target_result.domain
    target_registry = target_result.registry_source
    target_risk_tier = target_result.risk_tier
    
    # Get target server's axis scores
    axis_scores_query = text("""
        SELECT axis, label, p_top, p_critical, p_danger, probs
        FROM McpLlmAxisScore
        WHERE server_id = :server_id
    """)
    axis_results = session.execute(axis_scores_query, {"server_id": server_id}).fetchall()
    
    axes: dict[str, AxisScore] = {}
    for row in axis_results:
        axes[row.axis] = AxisScore(
            label=row.label,
            p_top=float(row.p_top) if row.p_top is not None else 0.0,
            p_critical=float(row.p_critical) if row.p_critical is not None else 0.0,
            p_danger=float(row.p_danger) if row.p_danger is not None else 0.0,
            probs=row.probs if isinstance(row.probs, dict) else {},
        )
    
    # Get up to 5 peer servers (same domain or registry_source, excluding target)
    peers_query = text("""
        SELECT s.id as server_id, s.name, s.risk_tier
        FROM McpServerRegistry s
        WHERE s.id != :server_id
          AND (s.domain = :domain OR s.registry_source = :registry)
          AND s.risk_tier IS NOT NULL
        LIMIT 5
    """)
    peer_results = session.execute(peers_query, {
        "server_id": server_id,
        "domain": target_domain,
        "registry": target_registry,
    }).fetchall()
    
    peers: list[PeerServer] = [
        PeerServer(server_id=r.server_id, name=r.name, risk_tier=r.risk_tier)
        for r in peer_results
    ]
    
    return AxisComparisonResponse(server_id=server_id, axes=axes, peers=peers)


if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    
    # In-memory SQLite for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT,
                registry_source TEXT,
                risk_tier TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                axis TEXT NOT NULL,
                label TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT
            )
        """))
        conn.commit()
    
    # Seed 3 servers with axis scores (7 axes each)
    SessionLocal = sessionmaker(bind=engine)
    
    with SessionLocal() as session:
        # Server 1 - target
        session.execute(text("""
            INSERT INTO McpServerRegistry (id, name, domain, registry_source, risk_tier)
            VALUES (1, 'Target Server', 'example.com', 'internal', 'medium')
        """))
        # Server 2 - peer with same domain
        session.execute(text("""
            INSERT INTO McpServerRegistry (id, name, domain, registry_source, risk_tier)
            VALUES (2, 'Peer Server 1', 'example.com', 'external', 'low')
        """))
        # Server 3 - peer with same registry
        session.execute(text("""
            INSERT INTO McpServerRegistry (id, name, domain, registry_source, risk_tier)
            VALUES (3, 'Peer Server 2', 'other.com', 'internal', 'high')
        """))
        
        axes = [
            ("accuracy", "Accuracy", 0.8, 0.1, 0.1),
            ("honesty", "Honesty", 0.7, 0.2, 0.1),
            ("safety", "Safety", 0.9, 0.05, 0.05),
            ("helpfulness", "Helpfulness", 0.75, 0.15, 0.1),
            ("truthfulness", "Truthfulness", 0.85, 0.1, 0.05),
            ("harmlessness", "Harmlessness", 0.95, 0.03, 0.02),
            ("refusal_quality", "Refusal Quality", 0.6, 0.25, 0.15),
        ]
        
        for server_id in [1, 2, 3]:
            for axis, label, p_top, p_critical, p_danger in axes:
                session.execute(text("""
                    INSERT INTO McpLlmAxisScore 
                    (server_id, axis, label, p_top, p_critical, p_danger, probs)
                    VALUES (:server_id, :axis, :label, :p_top, :p_critical, :p_danger, :probs)
                """), {
                    "server_id": server_id,
                    "axis": axis,
                    "label": label,
                    "p_top": p_top,
                    "p_critical": p_critical,
                    "p_danger": p_danger,
                    "probs": json.dumps({"top": p_top, "critical": p_critical, "danger": p_danger}),
                })
        session.commit()
    
    # Override dependency
    def override_get_session():
        yield SessionLocal()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    # Run test
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/servers/1/axis-comparison")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert "axes" in data, "Missing 'axes' in response"
    assert len(data["axes"]) == 7, f"Expected 7 axes, got {len(data['axes'])}"
    assert "accuracy" in data["axes"]
    assert "honesty" in data["axes"]
    assert "safety" in data["axes"]
    assert "helpfulness" in data["axes"]
    assert "truthfulness" in data["axes"]
    assert "harmlessness" in data["axes"]
    assert "refusal_quality" in data["axes"]
    
    assert "peers" in data, "Missing 'peers' in response"
    assert len(data["peers"]) >= 1, f"Expected at least 1 peer, got {len(data['peers'])}"
    
    print("PASS")