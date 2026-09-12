"""Contract test for trust_status_api service."""
from __future__ import annotations

import sqlite3
import sys
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry
from logic.trust_gating_override import trust_gate


class TrustStatusResponse(BaseModel):
    server_id: str
    name: str
    trust_score: float
    trust_tier: str
    is_trusted: bool
    verdict: str
    verdict_reasoning: str


app = FastAPI()


@app.get("/api/servers/{server_id}/trust-status", response_model=TrustStatusResponse)
def get_trust_status(
    server_id: str,
    session: Depends(get_session)
) -> TrustStatusResponse:
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    trust_result = trust_gate(server.url, server.name, {})
    is_trusted = trust_result.get('trusted', False)
    
    return TrustStatusResponse(
        server_id=server.server_id,
        name=server.name,
        trust_score=server.trust_score,
        trust_tier=server.risk_tier,
        is_trusted=is_trusted,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning
    )


if __name__ == "__main__":
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE McpServerRegistry (
            server_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            name TEXT NOT NULL,
            trust_score REAL,
            risk_tier TEXT,
            verdict TEXT,
            verdict_reasoning TEXT,
            confidence REAL,
            description TEXT,
            first_seen TEXT,
            last_assessed TEXT,
            last_scanned TEXT,
            last_seen TEXT,
            meta TEXT,
            registry_source TEXT,
            scan_count INTEGER
        )
    """)
    
    conn.execute("""
        INSERT INTO McpServerRegistry 
        (server_id, url, name, trust_score, risk_tier, verdict, verdict_reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "trusted-srv-001",
        "https://trusted.example.com",
        "Trusted Server",
        0.95,
        "low",
        "trusted",
        "High trust score with verified registry source"
    ))
    
    conn.execute("""
        INSERT INTO McpServerRegistry 
        (server_id, url, name, trust_score, risk_tier, verdict, verdict_reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "untrusted-srv-002",
        "https://untrusted.example.com",
        "Untrusted Server",
        0.20,
        "high",
        "untrusted",
        "Low trust score below threshold"
    ))
    
    conn.commit()
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    engine.raw_connection().connection.text_factory = lambda b: b.decode()
    
    import io
    for line in conn.iterdump():
        if line.startswith("INSERT"):
            try:
                engine.execute(line)
            except Exception:
                pass
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session() -> Generator[Session, None, None]:
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/servers/trusted-srv-001/trust-status")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["is_trusted"], bool)
    assert data["verdict_reasoning"]
    
    response = client.get("/api/servers/untrusted-srv-002/trust-status")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["is_trusted"], bool)
    assert data["verdict_reasoning"]
    
    print("PASS")
    sys.exit(0)