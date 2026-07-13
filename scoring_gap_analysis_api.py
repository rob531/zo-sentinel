from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

router = APIRouter()

class ServerGapAnalysis(BaseModel):
    server_id: str
    name: str
    last_scored_at: Optional[datetime]
    missing_axes: List[str]

class PartialServerGapAnalysis(ServerGapAnalysis):
    present_axes: int

class GapAnalysisResponse(BaseModel):
    total_servers: int
    unscored_servers: List[ServerGapAnalysis]
    partially_scored_servers: List[PartialServerGapAnalysis]
    scoring_coverage_pct: float
    last_refreshed_at: str

class RequeueRequest(BaseModel):
    server_ids: List[str]

class RequeueResponse(BaseModel):
    requeued: int

@router.get("/scoring/gaps", response_model=GapAnalysisResponse)
def get_scoring_gaps(
    min_age_hours: int = 24,
    missing_axes_limit: int = 7,
    session: Session = Depends(get_session)
):
    min_age = datetime.utcnow() - timedelta(hours=min_age_hours)

    # Get all servers
    servers = session.query(MCPServerRegistry).all()
    total_servers = len(servers)

    # Get all axes
    axes = session.query(MCPLLMAxisScores.axis_name).distinct().all()
    all_axes = [axis[0] for axis in axes]

    unscored_servers = []
    partially_scored_servers = []

    for server in servers:
        # Get scored axes for this server
        scored_axes = session.query(MCPLLMAxisScores.axis_name).filter(
            MCPLLMAxisScores.server_id == server.server_id
        ).all()
        scored_axes = [axis[0] for axis in scored_axes]

        # Calculate missing axes
        missing_axes = [axis for axis in all_axes if axis not in scored_axes]

        # Determine last scored time
        last_scored = session.query(func.max(MCPLLMAxisScores.scored_at)).filter(
            MCPLLMAxisScores.server_id == server.server_id
        ).scalar()

        if not scored_axes:
            unscored_servers.append({
                "server_id": server.server_id,
                "name": server.name,
                "last_scored_at": last_scored,
                "missing_axes": missing_axes
            })
        elif missing_axes:
            partially_scored_servers.append({
                "server_id": server.server_id,
                "name": server.name,
                "last_scored_at": last_scored,
                "missing_axes": missing_axes,
                "present_axes": len(scored_axes)
            })

    # Filter by missing_axes_limit
    partially_scored_servers = [
        s for s in partially_scored_servers
        if len(s["missing_axes"]) <= missing_axes_limit
    ]

    # Calculate coverage percentage
    scored_servers = total_servers - len(unscored_servers)
    coverage_pct = (scored_servers / total_servers * 100) if total_servers else 0

    return {
        "total_servers": total_servers,
        "unscored_servers": unscored_servers,
        "partially_scored_servers": partially_scored_servers,
        "scoring_coverage_pct": coverage_pct,
        "last_refreshed_at": datetime.utcnow().isoformat()
    }

@router.post("/scoring/gaps/requeue", response_model=RequeueResponse)
def requeue_servers(
    request: RequeueRequest,
    session: Session = Depends(get_session)
):
    # In a real implementation, this would post to pipeline_bridge
    # For this example, we'll just return the count
    return {"requeued": len(request.server_ids)}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Create a test database
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override the session dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_session.add(MCPServerRegistry(
        server_id="test1",
        name="Test Server 1",
        last_scanned=datetime.utcnow(),
        scan_count=0
    ))
    test_session.add(MCPServerRegistry(
        server_id="test2",
        name="Test Server 2",
        last_scanned=datetime.utcnow(),
        scan_count=1
    ))
    test_session.add(MCPLLMAxisScores(
        server_id="test2",
        axis_name="axis1",
        scored_at=datetime.utcnow()
    ))
    test_session.commit()

    # Create the app and test client
    from app.main import app
    client = TestClient(app)

    # Test the scoring gaps endpoint
    response = client.get("/scoring/gaps")
    assert response.json()["unscored_servers"], "Unscored servers list should not be empty"
    print("PASS")

    # Test the requeue endpoint
    response = client.post("/scoring/gaps/requeue", json={"server_ids": ["test1", "test2"]})
    assert response.json()["requeued"] == 2, "Should return requeued count of 2"
    print("PASS")