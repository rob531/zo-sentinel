from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi.testclient import TestClient
import requests

router = APIRouter()

class FreshnessSummary(BaseModel):
    stale_gt_7d: int
    stale_gt_24h: int
    healthy_lt_24h: int
    total_servers: int

class ServerFreshnessDetail(BaseModel):
    server_id: int
    last_scanned: Optional[datetime]
    last_seen: Optional[datetime]
    scan_count: int
    first_seen: datetime
    axis_max_scored_at: Optional[datetime]
    age_in_hours: Optional[float]

@router.get("/servers/freshness/summary", response_model=FreshnessSummary)
async def get_freshness_summary(db: Session = Depends(get_session)):
    now = datetime.utcnow()

    # Get server counts for each bucket
    healthy_lt_24h = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.last_seen >= now - timedelta(hours=24)
    ).count()

    stale_gt_24h = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.last_seen < now - timedelta(hours=24),
        MCPServerRegistry.last_seen >= now - timedelta(days=7)
    ).count()

    stale_gt_7d = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.last_seen < now - timedelta(days=7)
    ).count()

    total_servers = db.query(MCPServerRegistry).count()

    return FreshnessSummary(
        stale_gt_7d=stale_gt_7d,
        stale_gt_24h=stale_gt_24h,
        healthy_lt_24h=healthy_lt_24h,
        total_servers=total_servers
    )

@router.get("/servers/{server_id}/freshness", response_model=ServerFreshnessDetail)
async def get_server_freshness(server_id: int, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get max scored_at from axis scores
    axis_max_scored_at = db.query(func.max(MCPLLMAxisScores.scored_at)).filter(
        MCPLLMAxisScores.server_id == server_id
    ).scalar()

    # Calculate age_in_hours for last_seen
    age_in_hours = None
    if server.last_seen:
        age_in_hours = (datetime.utcnow() - server.last_seen).total_seconds() / 3600

    return ServerFreshnessDetail(
        server_id=server.id,
        last_scanned=server.last_scanned,
        last_seen=server.last_seen,
        scan_count=server.scan_count,
        first_seen=server.first_seen,
        axis_max_scored_at=axis_max_scored_at,
        age_in_hours=age_in_hours
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_db)
    TestSession = sessionmaker(bind=test_db)

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        # Add test server
        test_server = MCPServerRegistry(
            id=1,
            last_scanned=datetime.utcnow() - timedelta(hours=12),
            last_seen=datetime.utcnow() - timedelta(hours=6),
            scan_count=5,
            first_seen=datetime.utcnow() - timedelta(days=30)
        )
        session.add(test_server)

        # Add test axis score
        test_axis = MCPLLMAxisScores(
            server_id=1,
            scored_at=datetime.utcnow() - timedelta(hours=3),
            score=0.9
        )
        session.add(test_axis)
        session.commit()

    # Test endpoints
    client = TestClient(app)

    # Test summary endpoint
    summary_resp = client.get("/servers/freshness/summary")
    assert summary_resp.status_code == 200
    summary_data = summary_resp.json()
    assert summary_data["healthy_lt_24h"] == 1
    assert summary_data["stale_gt_24h"] == 0
    assert summary_data["stale_gt_7d"] == 0
    assert summary_data["total_servers"] == 1

    # Test detail endpoint
    detail_resp = client.get("/servers/1/freshness")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["age_in_hours"] == 6  # 6 hours old in test data

    print("PASS")