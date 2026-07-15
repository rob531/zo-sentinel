from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.db import get_session
from app.models import MCPServerRegistry
from sqlalchemy.orm import Session
import httpx
import json

router = APIRouter()

class ServerDetailResponse(BaseModel):
    server_id: str
    name: str
    url: str
    description: Optional[str] = None
    trust_score: float
    verdict: str
    verdict_reasoning: Optional[str] = None
    confidence: float
    last_assessed: str
    first_seen: str
    last_seen: str
    last_scanned: str
    scan_count: int
    risk_tier: str
    meta: dict

async def get_write_service_data(server_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}'"}
        )
        if response.status_code != 200:
            return None
        return response.json()

@router.get("/servers/{server_id}", response_model=ServerDetailResponse)
async def get_server_detail(server_id: str, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    write_service_data = await get_write_service_data(server_id)
    if write_service_data:
        signal_scores = write_service_data.get("data", [])
        if signal_scores:
            latest_score = signal_scores[0]
            server.trust_score = latest_score.get("trust_score", server.trust_score)
            server.verdict = latest_score.get("verdict", server.verdict)
            server.verdict_reasoning = latest_score.get("verdict_reasoning", server.verdict_reasoning)
            server.confidence = latest_score.get("confidence", server.confidence)
            server.last_assessed = latest_score.get("last_assessed", server.last_assessed)
            server.risk_tier = latest_score.get("risk_tier", server.risk_tier)

    return ServerDetailResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        description=server.description,
        trust_score=server.trust_score,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning,
        confidence=server.confidence,
        last_assessed=str(server.last_assessed),
        first_seen=str(server.first_seen),
        last_seen=str(server.last_seen),
        last_scanned=str(server.last_scanned),
        scan_count=server.scan_count,
        risk_tier=server.risk_tier,
        meta=server.meta if server.meta else {}
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test_server_1",
        name="Test Server",
        url="https://testserver.com",
        description="A test server",
        trust_score=0.8,
        verdict="safe",
        verdict_reasoning="Test reasoning",
        confidence=0.9,
        last_assessed="2023-01-01",
        first_seen="2023-01-01",
        last_seen="2023-01-01",
        last_scanned="2023-01-01",
        scan_count=1,
        risk_tier="low",
        meta={"key": "value"}
    )
    test_session.add(test_server)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test_server_1")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {
        "server_id", "name", "url", "description", "trust_score", "verdict",
        "verdict_reasoning", "confidence", "last_assessed", "first_seen",
        "last_seen", "last_scanned", "scan_count", "risk_tier", "meta"
    }
    print("PASS")