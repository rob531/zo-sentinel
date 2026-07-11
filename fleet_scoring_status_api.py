from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, desc
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class ServerScoringStatus(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    last_scored_at: Optional[datetime]
    stale_seconds: Optional[int]
    axes_stale_count: int
    needs_rescore_bool: bool

class FleetScoringStatusResponse(BaseModel):
    servers: List[ServerScoringStatus]

def get_stale_threshold() -> int:
    return 86400  # Default threshold: 24 hours in seconds

@router.get("/servers/scoring-status", response_model=FleetScoringStatusResponse)
async def get_fleet_scoring_status(
    session=Depends(get_session),
    threshold: Optional[int] = None
):
    threshold = threshold if threshold is not None else get_stale_threshold()

    # Get all servers
    servers = session.query(MCPServerRegistry).all()

    result = []
    for server in servers:
        # Get most recent scores for each axis
        subquery = (
            session.query(
                MCPLLMAxisScores.axis_name,
                func.max(MCPLLMAxisScores.scored_at).label('max_scored_at')
            )
            .filter(MCPLLMAxisScores.server_id == server.server_id)
            .group_by(MCPLLMAxisScores.axis_name)
            .subquery()
        )

        recent_scores = (
            session.query(MCPLLMAxisScores)
            .join(
                subquery,
                (MCPLLMAxisScores.axis_name == subquery.c.axis_name) &
                (MCPLLMAxisScores.scored_at == subquery.c.max_scored_at)
            )
            .all()
        )

        last_scored_at = None
        axes_stale_count = 0

        if recent_scores:
            # Find the most recent score across all axes
            last_scored_at = max(score.scored_at for score in recent_scores)

            # Count stale axes
            now = datetime.utcnow()
            axes_stale_count = sum(
                1 for score in recent_scores
                if (now - score.scored_at).total_seconds() > threshold
            )

        stale_seconds = (
            (datetime.utcnow() - last_scored_at).total_seconds()
            if last_scored_at else None
        )

        needs_rescore = (
            stale_seconds is not None and stale_seconds > threshold
        ) if stale_seconds is not None else True

        result.append(ServerScoringStatus(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
            last_scored_at=last_scored_at,
            stale_seconds=stale_seconds,
            axes_stale_count=axes_stale_count,
            needs_rescore_bool=needs_rescore
        ))

    return {"servers": result}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session as original_get_session

    # Create in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the get_session dependency
    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_session] = override_get_db

    # Create test app
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    # Seed test data
    test_client = TestClient(app)
    with TestSessionLocal() as session:
        # Add 3 test servers
        server1 = MCPServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="low"
        )
        server2 = MCPServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="medium"
        )
        server3 = MCPServerRegistry(
            server_id="server3",
            name="Test Server 3",
            risk_tier="high"
        )
        session.add_all([server1, server2, server3])

        # Add scores for server1 and server2 (server3 will be stale)
        from datetime import datetime, timedelta
        now = datetime.utcnow()

        # Server 1 - recently scored
        session.add(MCPLLMAxisScores(
            server_id="server1",
            axis_name="axis1",
            scored_at=now - timedelta(hours=1)
        ))
        session.add(MCPLLMAxisScores(
            server_id="server1",
            axis_name="axis2",
            scored_at=now - timedelta(hours=2)
        ))

        # Server 2 - recently scored
        session.add(MCPLLMAxisScores(
            server_id="server2",
            axis_name="axis1",
            scored_at=now - timedelta(hours=3)
        ))

        # Server 3 - no scores (will be stale)
        session.commit()

    # Test the endpoint
    response = test_client.get("/servers/scoring-status")
    assert response.status_code == 200

    data = response.json()
    stale_servers = [s for s in data["servers"] if s["needs_rescore_bool"]]

    # Should have 1 stale server (server3)
    assert len(stale_servers) == 1
    assert stale_servers[0]["server_id"] == "server3"

    print("PASS")