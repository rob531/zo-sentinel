from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import requests

from app.db import get_session
from app.models import McpThreatAssociation

router = APIRouter()

class ThreatAssociationDetail(BaseModel):
    mcp_name: str
    threat_id: str
    threat_source: str
    severity: str
    description: str
    associated_at: str

@router.get("/mcp/{mcp_id}/threat_associations/detail", response_model=List[ThreatAssociationDetail])
async def get_mcp_threat_associations_detail(mcp_id: str, db: Session = Depends(get_session)):
    query = """
    SELECT mcp_name, threat_id, threat_source, severity, description, associated_at
    FROM mcp_threat_associations
    WHERE mcp_id = :mcp_id
    """
    result = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": query, "params": {"mcp_id": mcp_id}}
    )
    result.raise_for_status()
    associations = result.json()
    if not associations:
        return []
    return [ThreatAssociationDetail(**assoc) for assoc in associations]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Override get_session dependency
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from app.main import app
    app.dependency_overrides[get_session] = override_get_session

    # Seed the database with test data
    def seed_db():
        db = TestingSessionLocal()
        test_associations = [
            McpThreatAssociation(
                mcp_id="test_mcp_1",
                mcp_name="Test MCP 1",
                threat_id="threat_1",
                threat_source="Source 1",
                severity="High",
                description="Test threat 1",
                associated_at="2023-01-01"
            ),
            McpThreatAssociation(
                mcp_id="test_mcp_2",
                mcp_name="Test MCP 2",
                threat_id="threat_2",
                threat_source="Source 2",
                severity="Medium",
                description="Test threat 2",
                associated_at="2023-01-02"
            )
        ]
        db.add_all(test_associations)
        db.commit()
        db.close()

    seed_db()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp/test_mcp_1/threat_associations/detail")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["threat_id"] == "threat_1"

    response = client.get("/mcp/test_mcp_3/threat_associations/detail")
    assert response.status_code == 200
    assert len(response.json()) == 0

    print("PASS")