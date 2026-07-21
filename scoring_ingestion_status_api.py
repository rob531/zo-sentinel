from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class ServerStatus(BaseModel):
    server_id: str
    name: str
    last_axis_score_at: Optional[datetime]
    axes_count: int
    current_registry_tier: Optional[str]
    current_registry_verdict: Optional[str]

class Summary(BaseModel):
    total_pending: int
    oldest_pending_hours: Optional[float]
    registry_tiers_affected: List[str]

class Response(BaseModel):
    pending_servers: List[ServerStatus]
    summary: Summary

def get_pending_servers(db: Session) -> List[ServerStatus]:
    # Get servers with axis scores but no reflected registry updates
    servers_with_scores = db.query(
        MCPServerRegistry.id,
        MCPServerRegistry.name,
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.verdict,
        MCPServerRegistry.updated_at,
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.created_at
    ).join(
        MCPLLMAxisScores,
        MCPServerRegistry.id == MCPLLMAxisScores.server_id,
        isouter=True
    ).filter(
        MCPLLMAxisScores.server_id.isnot(None)
    ).all()

    pending_servers = []
    for server in servers_with_scores:
        if server.MCPLLMAxisScores:
            last_score_time = server.MCPLLMAxisScores.created_at
            if server.updated_at and last_score_time and last_score_time > server.updated_at:
                pending_servers.append({
                    'server_id': server.id,
                    'name': server.name,
                    'last_axis_score_at': last_score_time,
                    'axes_count': 1,  # Assuming one axis score per server for simplicity
                    'current_registry_tier': server.risk_tier,
                    'current_registry_verdict': server.verdict
                })

    return pending_servers

@router.get("/scoring/ingestion-status", response_model=Response)
async def get_ingestion_status(db: Session = Depends(get_session)):
    pending_servers = get_pending_servers(db)

    if not pending_servers:
        return Response(pending_servers=[], summary=Summary(total_pending=0, oldest_pending_hours=None, registry_tiers_affected=[]))

    # Calculate summary
    total_pending = len(pending_servers)
    oldest_pending = min(server['last_axis_score_at'] for server in pending_servers)
    oldest_pending_hours = (datetime.now() - oldest_pending).total_seconds() / 3600
    registry_tiers_affected = list(set(server['current_registry_tier'] for server in pending_servers if server['current_registry_tier']))

    return Response(
        pending_servers=pending_servers,
        summary=Summary(
            total_pending=total_pending,
            oldest_pending_hours=oldest_pending_hours,
            registry_tiers_affected=registry_tiers_affected
        )
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the dependency for testing
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Server A has stale axis scores
        session.add(MCPServerRegistry(
            id="A",
            name="Server A",
            risk_tier="high",
            verdict="malicious",
            updated_at=datetime(2023, 1, 1)
        ))
        session.add(MCPLLMAxisScores(
            server_id="A",
            created_at=datetime(2023, 1, 2)
        ))

        # Servers B and C are up-to-date
        session.add(MCPServerRegistry(
            id="B",
            name="Server B",
            risk_tier="medium",
            verdict="suspicious",
            updated_at=datetime(2023, 1, 3)
        ))
        session.add(MCPLLMAxisScores(
            server_id="B",
            created_at=datetime(2023, 1, 1)
        ))

        session.add(MCPServerRegistry(
            id="C",
            name="Server C",
            risk_tier="low",
            verdict="clean",
            updated_at=datetime(2023, 1, 3)
        ))
        session.add(MCPLLMAxisScores(
            server_id="C",
            created_at=datetime(2023, 1, 1)
        ))

        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/scoring/ingestion-status")
    assert response.status_code == 200
    data = response.json()

    assert len(data["pending_servers"]) == 1
    assert data["pending_servers"][0]["server_id"] == "A"
    assert data["summary"]["total_pending"] == 1
    assert data["summary"]["oldest_pending_hours"] is not None
    assert "high" in data["summary"]["registry_tiers_affected"]

    print("PASS")