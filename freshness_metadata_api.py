from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class FreshnessMetadata(BaseModel):
    server_id: str
    last_updated: str
    freshness_score: float

@router.get("/freshness/metadata", response_model=List[FreshnessMetadata])
def get_freshness_metadata(db: Session = Depends(get_session)):
    servers = db.query(
        MCPServerRegistry.server_id,
        MCPServerRegistry.last_updated,
        MCPServerRegistry.freshness_score
    ).all()

    return [
        {
            "server_id": server.server_id,
            "last_updated": server.last_updated.isoformat() if server.last_updated else None,
            "freshness_score": server.freshness_score
        }
        for server in servers
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        session.add_all([
            MCPServerRegistry(
                server_id="server1",
                last_updated="2023-01-01T00:00:00",
                freshness_score=0.95
            ),
            MCPServerRegistry(
                server_id="server2",
                last_updated="2023-01-02T00:00:00",
                freshness_score=0.85
            )
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/freshness/metadata")
    assert response.status_code == 200
    assert response.json() == [
        {
            "server_id": "server1",
            "last_updated": "2023-01-01T00:00:00",
            "freshness_score": 0.95
        },
        {
            "server_id": "server2",
            "last_updated": "2023-01-02T00:00:00",
            "freshness_score": 0.85
        }
    ]

    print("PASS")