from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPLLMAxisScore, MCPServerRegistry

router = APIRouter()

class AxisScoreStatus(BaseModel):
    score_count: int
    latest_score_at: Optional[datetime]

class ServerScoringStatus(BaseModel):
    server_id: str
    name: str
    unscored: bool
    last_scored_at: Optional[datetime]
    scan_count: int
    registry_source: str
    axes: dict[str, AxisScoreStatus]
    overall_scored: bool
    fresh: bool

class ServerScoringStatusResponse(BaseModel):
    status: ServerScoringStatus

class UnscoredServersResponse(BaseModel):
    unscored_servers: List[ServerScoringStatus]

def get_server_scoring_status(
    server_id: str,
    session: Session = Depends(get_session)
) -> ServerScoringStatus:
    server = session.query(MCPServerRegistry).filter_by(server_id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get scoring status for each axis
    axis_scores = session.query(
        MCPLLMAxisScore.axis_name,
        func.count(MCPLLMAxisScore.id).label('score_count'),
        func.max(MCPLLMAxisScore.created_at).label('latest_score_at')
    ).filter(
        MCPLLMAxisScore.server_id == server_id
    ).group_by(
        MCPLLMAxisScore.axis_name
    ).all()

    axes = {
        axis_name: {
            'score_count': score_count,
            'latest_score_at': latest_score_at
        }
        for axis_name, score_count, latest_score_at in axis_scores
    }

    # Determine if server is unscored (no scores in last 7 days)
    unscored = True
    last_scored_at = None
    for axis in axes.values():
        if axis['latest_score_at'] and axis['latest_score_at'] >= datetime.utcnow() - timedelta(days=7):
            unscored = False
            if axis['latest_score_at'] > last_scored_at:
                last_scored_at = axis['latest_score_at']

    # Determine if server is fresh (scanned in last 24 hours)
    fresh = server.last_scan_at and server.last_scan_at >= datetime.utcnow() - timedelta(hours=24)

    # Determine if server is overall scored (at least one score in any axis)
    overall_scored = any(axis['score_count'] > 0 for axis in axes.values())

    return ServerScoringStatus(
        server_id=server.server_id,
        name=server.name,
        unscored=unscored,
        last_scored_at=last_scored_at,
        scan_count=server.scan_count,
        registry_source=server.registry_source,
        axes=axes,
        overall_scored=overall_scored,
        fresh=fresh
    )

def get_unscored_servers(
    session: Session = Depends(get_session)
) -> List[ServerScoringStatus]:
    # Get all servers
    servers = session.query(MCPServerRegistry).all()

    unscored_servers = []
    for server in servers:
        status = get_server_scoring_status(server.server_id, session)
        if status.unscored:
            unscored_servers.append(status)

    return unscored_servers

@router.get("/servers/{server_id}/scoring-status", response_model=ServerScoringStatusResponse)
async def get_scoring_status(
    server_id: str,
    session: Session = Depends(get_session)
):
    status = get_server_scoring_status(server_id, session)
    return {"status": status}

@router.get("/servers/scoring-status/unscored", response_model=UnscoredServersResponse)
async def get_unscored_servers_list(
    session: Session = Depends(get_session)
):
    unscored_servers = get_unscored_servers(session)
    return {"unscored_servers": unscored_servers}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test_server",
        name="Test Server",
        scan_count=5,
        registry_source="test",
        last_scan_at=datetime.utcnow() - timedelta(hours=2)
    )
    test_session.add(test_server)
    test_session.commit()

    # Create a test client
    client = TestClient(router)

    # Test unscored server returns fresh=False
    response = client.get("/servers/test_server/scoring-status")
    assert response.status_code == 200
    assert response.json()["status"]["fresh"] is False

    # Test unscored_only returns list with that server
    response = client.get("/servers/scoring-status/unscored")
    assert response.status_code == 200
    assert len(response.json()["unscored_servers"]) == 1
    assert response.json()["unscored_servers"][0]["server_id"] == "test_server"

    print("PASS")