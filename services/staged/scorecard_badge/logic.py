"""scorecard_badge service - returns compact JSON badge for server."""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from write_service import write_service

router = APIRouter(prefix="/api", tags=["scorecard_badge"])


class BadgeResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    trust_score: Optional[float] = None
    confidence: Optional[float] = None
    last_assessed: Optional[datetime] = None
    overall_risk_score: Optional[float] = None


def get_server_badge(session: Session, server_id: str) -> Dict[str, Any]:
    """Retrieve server badge data combining registry and axis scores."""
    result = session.execute(
        text("""
            SELECT 
                sr.server_id,
                sr.name,
                sr.risk_tier,
                sr.verdict,
                sr.trust_score,
                sr.confidence,
                sr.last_assessed,
                ax.p_top as overall_risk_score
            FROM McpServerRegistry sr
            LEFT JOIN McpLlmAxisScore ax ON ax.server_id = sr.server_id
                AND ax.axis = 'overall_risk'
            WHERE sr.server_id = :server_id
        """),
        {"server_id": server_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    return {
        "server_id": row.server_id,
        "name": row.name,
        "risk_tier": row.risk_tier,
        "verdict": row.verdict,
        "trust_score": row.trust_score,
        "confidence": row.confidence,
        "last_assessed": row.last_assessed,
        "overall_risk_score": row.overall_risk_score,
    }


@router.get("/servers/{server_id}/badge", response_model=BadgeResponse)
def get_badge(
    server_id: str,
    session: Session = Depends(get_session)
) -> BadgeResponse:
    """Get compact JSON badge for a server."""
    badge_data = get_server_badge(session, server_id)
    return BadgeResponse(**badge_data)


if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    def create_tables(conn):
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                verdict TEXT,
                trust_score REAL,
                confidence REAL,
                last_assessed TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                p_top REAL,
                UNIQUE(server_id, axis)
            )
        """))
        conn.commit()
    
    def seed_data(conn):
        conn.execute(text("""
            INSERT OR REPLACE INTO McpServerRegistry 
            (server_id, name, risk_tier, verdict, trust_score, confidence, last_assessed)
            VALUES 
            ('srv-001', 'Test Server Alpha', 'low', 'approved', 0.85, 0.92, '2024-01-15 10:00:00'),
            ('srv-002', 'Test Server Beta', 'medium', 'pending', 0.62, 0.78, '2024-01-14 08:30:00')
        """))
        conn.execute(text("""
            INSERT OR REPLACE INTO McpLlmAxisScore (server_id, axis, p_top)
            VALUES 
            ('srv-001', 'overall_risk', 0.15),
            ('srv-002', 'overall_risk', 0.55)
        """))
        conn.commit()
    
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    seed_data(conn)
    
    engine = create_engine("sqlite:///:memory:", row_factory=lambda r: r)
    
    in_memory_conn = sqlite3.connect(":memory:")
    in_memory_conn.row_factory = sqlite3.Row
    create_tables(in_memory_conn)
    seed_data(in_memory_conn)
    
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with in_memory_conn:
        in_memory_conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                verdict TEXT,
                trust_score REAL,
                confidence REAL,
                last_assessed TIMESTAMP
            )
        """))
        in_memory_conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                p_top REAL,
                UNIQUE(server_id, axis)
            )
        """))
    
    for row in conn.execute(text("SELECT * FROM McpServerRegistry")):
        in_memory_conn.execute(
            text("INSERT INTO McpServerRegistry VALUES (:s, :n, :r, :v, :t, :c, :l)"),
            {"s": row["server_id"], "n": row["name"], "r": row["risk_tier"],
             "v": row["verdict"], "t": row["trust_score"], "c": row["confidence"],
             "l": row["last_assessed"]}
        )
    for row in conn.execute(text("SELECT * FROM McpLlmAxisScore")):
        in_memory_conn.execute(
            text("INSERT INTO McpLlmAxisScore (server_id, axis, p_top) VALUES (:s, :a, :p)"),
            {"s": row["server_id"], "a": row["axis"], "p": row["p_top"]}
        )
    in_memory_conn.commit()
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with test_engine.connect() as test_conn:
        test_conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                verdict TEXT,
                trust_score REAL,
                confidence REAL,
                last_assessed TIMESTAMP
            )
        """))
        test_conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                p_top REAL,
                UNIQUE(server_id, axis)
            )
        """))
        test_conn.commit()
    
    with test_engine.connect() as test_conn:
        for row in conn.execute(text("SELECT * FROM McpServerRegistry")).fetchall():
            test_conn.execute(
                text("INSERT INTO McpServerRegistry VALUES (:s, :n, :r, :v, :t, :c, :l)"),
                {"s": row["server_id"], "n": row["name"], "r": row["risk_tier"],
                 "v": row["verdict"], "t": row["trust_score"], "c": row["confidence"],
                 "l": row["last_assessed"]}
            )
        for row in conn.execute(text("SELECT * FROM McpLlmAxisScore")).fetchall():
            test_conn.execute(
                text("INSERT INTO McpLlmAxisScore (server_id, axis, p_top) VALUES (:s, :a, :p)"),
                {"s": row["server_id"], "a": row["axis"], "p": row["p_top"]}
            )
        test_conn.commit()
    
    TestingSessionLocal = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app = router
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    response = client.get("/api/servers/srv-001/badge")
    
    if response.status_code != 200:
        print(f"FAIL: expected 200, got {response.status_code}")
        print(response.text)
        exit(1)
    
    data = response.json()
    
    if "risk_tier" not in data:
        print("FAIL: risk_tier not in response")
        exit(1)
    
    if "verdict" not in data:
        print("FAIL: verdict not in response")
        exit(1)
    
    if data.get("risk_tier") != "low":
        print(f"FAIL: expected risk_tier 'low', got {data.get('risk_tier')}")
        exit(1)
    
    if data.get("verdict") != "approved":
        print(f"FAIL: expected verdict 'approved', got {data.get('verdict')}")
        exit(1)
    
    if data.get("server_id") != "srv-001":
        print(f"FAIL: expected server_id 'srv-001', got {data.get('server_id')}")
        exit(1)
    
    if data.get("overall_risk_score") is None:
        print("FAIL: overall_risk_score not in response")
        exit(1)
    
    conn.close()
    in_memory_conn.close()
    
    print("PASS")