from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy import func, and_
import requests

router = APIRouter()

class ServerInfo(BaseModel):
    server_id: int
    name: str
    registry_source: str
    first_seen: datetime
    last_scanned: datetime

class UnscoredServersResponse(BaseModel):
    servers: List[ServerInfo]
    total: int
    lookback_days: int

def get_unscored_servers(
    session=Depends(get_session),
    lookback_days: int = 30
) -> UnscoredServersResponse:
    # Get current timestamp and lookback timestamp
    now = datetime.utcnow()
    lookback_timestamp = now - timedelta(days=lookback_days)

    # Query for servers with no scores or expired scores
    subquery = session.query(
        MCPLLMAxisScores.server_id,
        func.max(MCPLLMAxisScores.created_at).label('max_score_time')
    ).group_by(
        MCPLLMAxisScores.server_id
    ).subquery()

    query = session.query(MCPServerRegistry).outerjoin(
        subquery,
        MCPServerRegistry.server_id == subquery.c.server_id
    ).filter(
        and_(
            subquery.c.server_id.is_(None) |
            (subquery.c.max_score_time < lookback_timestamp),
            MCPServerRegistry.deleted_at.is_(None)
        )
    ).order_by(
        MCPServerRegistry.server_id
    )

    servers = query.all()

    # Convert to response format
    server_infos = [
        ServerInfo(
            server_id=server.server_id,
            name=server.name,
            registry_source=server.registry_source,
            first_seen=server.first_seen,
            last_scanned=server.last_scanned
        ) for server in servers
    ]

    return UnscoredServersResponse(
        servers=server_infos,
        total=len(servers),
        lookback_days=lookback_days
    )

@router.get("/servers/unscored", response_model=UnscoredServersResponse)
async def unscored_servers(
    lookback_days: Optional[int] = 30
):
    return get_unscored_servers(lookback_days=lookback_days)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    with TestSession() as session:
        # Add scored server
        scored_server = MCPServerRegistry(
            server_id=1,
            name="Scored Server",
            registry_source="test",
            first_seen=datetime.utcnow(),
            last_scanned=datetime.utcnow()
        )
        session.add(scored_server)
        session.add(MCPLLMAxisScores(
            server_id=1,
            created_at=datetime.utcnow()
        ))

        # Add unscored server
        unscored_server = MCPServerRegistry(
            server_id=2,
            name="Unscored Server",
            registry_source="test",
            first_seen=datetime.utcnow(),
            last_scanned=datetime.utcnow()
        )
        session.add(unscored_server)

        session.commit()

    # Test client
    client = TestClient(app)

    # Test unscored servers endpoint
    response = client.get("/servers/unscored")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["servers"][0]["name"] == "Unscored Server"

    # Test with lookback
    response = client.get("/servers/unscored?lookback_days=1")
    assert response.status_code == 200
    assert response.json()["total"] == 2

    print("PASS")