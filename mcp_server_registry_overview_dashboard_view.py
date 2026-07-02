from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpScoreDisputes
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

router = APIRouter()

class ServerDetail(BaseModel):
    server_id: int
    name: str
    registry_source: str
    url: str
    description: str
    trust_score: float
    verdict: str
    confidence: float
    last_assessed: datetime
    risk_tier: str

class ServerRegistryOverview(BaseModel):
    servers: List[ServerDetail]

def get_mcp_server_registry_overview(session: Session = Depends(get_session)) -> ServerRegistryOverview:
    servers = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.url,
        McpServerRegistry.description,
        McpServerRegistry.trust_score,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
        McpServerRegistry.last_assessed,
        McpServerRegistry.risk_tier
    ).all()

    server_details = []
    for server in servers:
        server_detail = ServerDetail(
            server_id=server.server_id,
            name=server.name,
            registry_source=server.registry_source,
            url=server.url,
            description=server.description,
            trust_score=server.trust_score,
            verdict=server.verdict,
            confidence=server.confidence,
            last_assessed=server.last_assessed,
            risk_tier=server.risk_tier
        )

        # Check for rule-override
        critical_axis = session.query(McpLlmAxisScores).filter(
            McpLlmAxisScores.server_id == server.server_id,
            McpLlmAxisScores.axis == 'CRITICAL'
        ).first()

        if critical_axis:
            dispute = session.query(McpScoreDisputes).filter(
                McpScoreDisputes.server_id == server.server_id,
                McpScoreDisputes.axis == 'CRITICAL'
            ).first()

            if not dispute or dispute.resolved:
                server_detail.risk_tier = 'CRITICAL'

        server_details.append(server_detail)

    return ServerRegistryOverview(servers=server_details)

@router.get("/dashboard/mcp-server-registry-overview", response_model=ServerRegistryOverview)
async def dashboard_mcp_server_registry_overview(overview: ServerRegistryOverview = Depends(get_mcp_server_registry_overview)):
    return overview

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_dashboard_mcp_server_registry_overview():
        db = next(override_get_session())
        server = McpServerRegistry(
            name="Test Server",
            registry_source="Test Source",
            url="http://test.com",
            description="Test Description",
            trust_score=0.9,
            verdict="Test Verdict",
            confidence=0.8,
            last_assessed=datetime.now(),
            risk_tier="MEDIUM"
        )
        db.add(server)
        db.commit()
        db.refresh(server)

        critical_axis = McpLlmAxisScores(
            server_id=server.server_id,
            axis="CRITICAL",
            score=0.7
        )
        db.add(critical_axis)
        db.commit()

        response = client.get("/dashboard/mcp-server-registry-overview")
        assert response.status_code == 200
        data = response.json()
        assert len(data["servers"]) == 1
        assert data["servers"][0]["name"] == "Test Server"
        assert data["servers"][0]["risk_tier"] == "CRITICAL"

    app.dependency_overrides[get_session] = override_get_session
    test_dashboard_mcp_server_registry_overview()
    print("PASS")