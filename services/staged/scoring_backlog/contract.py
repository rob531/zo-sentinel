from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session
import requests
from sqlalchemy import func

app = FastAPI()

class ServerInfo(BaseModel):
    server_id: int
    name: str
    url: str
    registry_source: str
    first_seen: str
    scan_count: int

class BacklogResponse(BaseModel):
    total: int
    servers: List[ServerInfo]

def get_unscored_servers(db: Session = Depends(get_session)) -> BacklogResponse:
    query = db.query(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.url,
        McpServerRegistry.registry_source,
        McpServerRegistry.first_seen,
        McpServerRegistry.scan_count
    ).outerjoin(
        McpLlmAxisScore,
        McpServerRegistry.server_id == McpLlmAxisScore.server_id
    ).filter(
        McpLlmAxisScore.scored_at.is_(None)
    ).all()

    servers = [
        ServerInfo(
            server_id=server.server_id,
            name=server.name,
            url=server.url,
            registry_source=server.registry_source,
            first_seen=str(server.first_seen),
            scan_count=server.scan_count
        ) for server in query
    ]

    return BacklogResponse(total=len(servers), servers=servers)

@app.get("/api/scoring/backlog", response_model=BacklogResponse)
async def scoring_backlog():
    return get_unscored_servers()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Seed test data
    test_servers = [
        McpServerRegistry(
            server_id=1,
            name="Test Server 1",
            url="http://test1.example.com",
            registry_source="test",
            first_seen="2023-01-01",
            scan_count=5
        ),
        McpServerRegistry(
            server_id=2,
            name="Test Server 2",
            url="http://test2.example.com",
            registry_source="test",
            first_seen="2023-01-02",
            scan_count=3
        ),
        McpServerRegistry(
            server_id=3,
            name="Test Server 3",
            url="http://test3.example.com",
            registry_source="test",
            first_seen="2023-01-03",
            scan_count=1
        )
    ]
    test_session.add_all(test_servers)
    test_session.commit()

    # Add scored server
    test_session.add(McpLlmAxisScore(
        server_id=1,
        scored_at="2023-01-04"
    ))
    test_session.commit()

    # Run test
    test_client = TestClient(app)
    response = test_client.get("/api/scoring/backlog")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {server["server_id"] for server in data["servers"]} == {2, 3}

    print("PASS")