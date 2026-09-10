"""
Contract for risk_distribution_summary service.
"""
import sys
from collections import Counter
from typing import Dict, Any
from contextlib import contextmanager

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Import the real data layer
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


# Pydantic models for response
class DistributionResponse(BaseModel):
    distribution: Dict[str, int]
    total_servers: int


# Logic layer
def compute_risk_distribution(session: Session) -> Dict[str, Any]:
    """Compute risk_tier distribution across all servers."""
    # Query all servers and their risk_tier values
    query = text("""
        SELECT risk_tier, COUNT(*) as count
        FROM McpServerRegistry
        WHERE risk_tier IS NOT NULL
        GROUP BY risk_tier
        ORDER BY risk_tier
    """)
    
    result = session.execute(query)
    rows = result.fetchall()
    
    distribution = {row[0]: row[1] for row in rows}
    total_servers = sum(distribution.values())
    
    return {
        "distribution": distribution,
        "total_servers": total_servers
    }


# Router
def create_router() -> FastAPI:
    app = FastAPI(title="risk_distribution_summary")
    
    @app.get("/api/risk/distribution", response_model=DistributionResponse)
    def get_distribution(session: Session = Depends(get_session)) -> DistributionResponse:
        result = compute_risk_distribution(session)
        return DistributionResponse(**result)
    
    return app


# Main app for testing
app = create_router()


def run_self_test():
    """Self-test using SQLite in-memory database."""
    # Create in-memory SQLite database
    sqlite_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create tables
    with sqlite_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                server_name TEXT,
                risk_tier TEXT,
                org_id TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpLlmAxisScore (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT,
                score REAL,
                created_at TIMESTAMP
            )
        """))
        conn.commit()
    
    # Seed test data with known tier distribution
    with sqlite_engine.connect() as conn:
        # Insert test servers with specific risk_tier distribution
        test_servers = [
            ("srv1", "Server 1", "critical", "org1"),
            ("srv2", "Server 2", "critical", "org1"),
            ("srv3", "Server 3", "high", "org1"),
            ("srv4", "Server 4", "high", "org1"),
            ("srv5", "Server 5", "high", "org1"),
            ("srv6", "Server 6", "medium", "org1"),
            ("srv7", "Server 7", "medium", "org1"),
            ("srv8", "Server 8", "low", "org1"),
            ("srv9", "Server 9", "low", "org1"),
            ("srv10", "Server 10", "low", "org1"),
        ]
        
        for server_id, name, tier, org_id in test_servers:
            conn.execute(
                text("""
                    INSERT INTO McpServerRegistry 
                    (server_id, server_name, risk_tier, org_id)
                    VALUES (:server_id, :server_name, :risk_tier, :org_id)
                """),
                {"server_id": server_id, "server_name": name, "risk_tier": tier, "org_id": org_id}
            )
        conn.commit()
    
    # Create session factory for SQLite
    TestingSessionLocal = sessionmaker(bind=sqlite_engine)
    
    @contextmanager
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    # Override dependency
    app.dependency_overrides[get_session] = override_get_session
    
    # Run test
    client = TestClient(app)
    response = client.get("/api/risk/distribution")
    
    if response.status_code != 200:
        print(f"FAIL: status_code={response.status_code}")
        return False
    
    data = response.json()
    
    # Verify distribution counts
    expected_distribution = {
        "critical": 2,
        "high": 3,
        "medium": 2,
        "low": 3
    }
    expected_total = 10
    
    if data["distribution"] != expected_distribution:
        print(f"FAIL: distribution mismatch. Expected {expected_distribution}, got {data['distribution']}")
        return False
    
    if data["total_servers"] != expected_total:
        print(f"FAIL: total_servers mismatch. Expected {expected_total}, got {data['total_servers']}")
        return False
    
    print("PASS")
    return True


if __name__ == "__main__":
    success = run_self_test()
    sys.exit(0 if success else 1)