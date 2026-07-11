from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class RegistryFreshness(BaseModel):
    first_seen: datetime
    last_seen_ago_s: int
    last_scanned_ago_s: int
    scan_count: int

class ScoringFreshness(BaseModel):
    last_scored_ago_s: int
    axes_covered: int
    all_axes_scored: bool

class ServerFreshnessResponse(BaseModel):
    server_id: str
    registry_freshness: RegistryFreshness
    scoring_freshness: ScoringFreshness
    risk_tier: Optional[str]
    staleness_flag: bool

class FleetFreshnessSummary(BaseModel):
    servers: List[ServerFreshnessResponse]
    total_servers: int
    stale_servers: int
    avg_last_scanned_ago_s: float
    avg_last_scored_ago_s: float

def calculate_freshness(server: MCPServerRegistry, scores: List[MCPLLMAxisScores], db: Session) -> ServerFreshnessResponse:
    now = datetime.utcnow()
    last_seen_ago = (now - server.last_seen).total_seconds()
    last_scanned_ago = (now - server.last_scanned).total_seconds() if server.last_scanned else float('inf')

    axes_covered = len(scores)
    all_axes_scored = axes_covered >= 3  # Assuming 3 axes are required

    last_scored_ago = min((now - score.scored_at).total_seconds() for score in scores) if scores else float('inf')

    staleness_flag = last_scored_ago > 86400 or last_scanned_ago > 604800

    return ServerFreshnessResponse(
        server_id=server.server_id,
        registry_freshness=RegistryFreshness(
            first_seen=server.first_seen,
            last_seen_ago_s=int(last_seen_ago),
            last_scanned_ago_s=int(last_scanned_ago),
            scan_count=server.scan_count
        ),
        scoring_freshness=ScoringFreshness(
            last_scored_ago_s=int(last_scored_ago),
            axes_covered=axes_covered,
            all_axes_scored=all_axes_scored
        ),
        risk_tier=server.risk_tier,
        staleness_flag=staleness_flag
    )

@router.get("/servers/{server_id}/freshness", response_model=ServerFreshnessResponse)
async def get_server_freshness(server_id: str, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    return calculate_freshness(server, scores, db)

@router.get("/servers/freshness/summary", response_model=FleetFreshnessSummary)
async def get_fleet_freshness_summary(
    limit: int = 50,
    risk_tier_filter: Optional[str] = None,
    db: Session = Depends(get_session)
):
    query = db.query(MCPServerRegistry)
    if risk_tier_filter:
        query = query.filter(MCPServerRegistry.risk_tier == risk_tier_filter)

    servers = query.limit(limit).all()
    server_ids = [server.server_id for server in servers]

    scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id.in_(server_ids)).all()
    scores_by_server = {score.server_id: [] for score in scores}
    for score in scores:
        scores_by_server[score.server_id].append(score)

    responses = []
    stale_count = 0
    total_last_scanned = 0
    total_last_scored = 0

    for server in servers:
        server_scores = scores_by_server.get(server.server_id, [])
        response = calculate_freshness(server, server_scores, db)
        responses.append(response)

        if response.staleness_flag:
            stale_count += 1

        total_last_scanned += response.registry_freshness.last_scanned_ago_s
        total_last_scored += response.scoring_freshness.last_scored_ago_s

    avg_last_scanned = total_last_scanned / len(servers) if servers else 0
    avg_last_scored = total_last_scored / len(servers) if servers else 0

    return FleetFreshnessSummary(
        servers=responses,
        total_servers=len(servers),
        stale_servers=stale_count,
        avg_last_scanned_ago_s=avg_last_scanned,
        avg_last_scored_ago_s=avg_last_scored
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    test_engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_server = MCPServerRegistry(
        server_id="test-server-1",
        first_seen=datetime.utcnow() - timedelta(days=10),
        last_seen=datetime.utcnow(),
        last_scanned=datetime.utcnow() - timedelta(hours=1),
        scan_count=5,
        risk_tier="low"
    )
    test_session.add(test_server)
    test_session.commit()

    test_score = MCPLLMAxisScores(
        server_id="test-server-1",
        axis="test-axis",
        scored_at=datetime.utcnow() - timedelta(hours=1),
        score=0.8
    )
    test_session.add(test_score)
    test_session.commit()

    # Test client
    client = TestClient(app)

    # Test single server freshness
    response = client.get("/servers/test-server-1/freshness")
    assert response.status_code == 200
    assert response.json()["staleness_flag"] is False

    # Test fleet summary
    response = client.get("/servers/freshness/summary")
    assert response.status_code == 200
    assert response.json()["total_servers"] == 1
    assert response.json()["stale_servers"] == 0

    # Test stale server
    stale_server = MCPServerRegistry(
        server_id="stale-server-1",
        first_seen=datetime.utcnow() - timedelta(days=10),
        last_seen=datetime.utcnow(),
        last_scanned=datetime.utcnow() - timedelta(days=10),
        scan_count=5,
        risk_tier="low"
    )
    test_session.add(stale_server)
    test_session.commit()

    stale_score = MCPLLMAxisScores(
        server_id="stale-server-1",
        axis="test-axis",
        scored_at=datetime.utcnow() - timedelta(days=2),
        score=0.8
    )
    test_session.add(stale_score)
    test_session.commit()

    response = client.get("/servers/stale-server-1/freshness")
    assert response.status_code == 200
    assert response.json()["staleness_flag"] is True

    print("PASS")