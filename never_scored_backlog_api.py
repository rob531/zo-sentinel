from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

router = APIRouter()

class ServerInfo(BaseModel):
    server_id: str
    name: str
    first_seen: datetime
    registry_source: str
    days_pending: int

class NeverScoredResponse(BaseModel):
    servers: List[ServerInfo]
    total_count: int
    as_of: str

def get_never_scored_servers(db: Session) -> List[ServerInfo]:
    """Get servers present in registry but absent from scores."""
    subquery = db.query(McpLlmAxisScores.server_id).subquery()
    servers = (
        db.query(McpServerRegistry)
        .filter(~McpServerRegistry.server_id.in_(subquery))
        .order_by(McpServerRegistry.first_seen.asc())
        .all()
    )

    now = datetime.utcnow()
    return [
        ServerInfo(
            server_id=server.server_id,
            name=server.name,
            first_seen=server.first_seen,
            registry_source=server.registry_source,
            days_pending=(now - server.first_seen).days,
        )
        for server in servers
    ]

@router.get("/servers/never-scored", response_model=NeverScoredResponse)
async def never_scored_backlog(db: Session = Depends(get_session)) -> NeverScoredResponse:
    servers = get_never_scored_servers(db)
    return NeverScoredResponse(
        servers=servers,
        total_count=len(servers),
        as_of=datetime.utcnow().isoformat(),
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, get_session
    from app.models import McpServerRegistry, McpLlmAxisScores
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    with TestSession() as session:
        # Add a server that hasn't been scored
        unscored_server = McpServerRegistry(
            server_id="test_server_1",
            name="Test Server 1",
            first_seen=datetime.utcnow() - timedelta(days=5),
            registry_source="test_source",
        )
        session.add(unscored_server)
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/servers/never-scored")
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) > 0
    assert data["total_count"] == len(data["servers"])
    print("PASS")