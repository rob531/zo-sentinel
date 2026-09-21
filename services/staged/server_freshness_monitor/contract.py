from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry

class ServerFreshnessResponse(BaseModel):
    server_id: str
    freshness_score: float
    last_scanned: datetime
    last_seen: datetime

def calculate_freshness_score(last_scanned: datetime, last_seen: datetime) -> float:
    now = datetime.utcnow()
    scanned_recency = (now - last_scanned).total_seconds()
    seen_recency = (now - last_seen).total_seconds()

    # Normalize to 0-1 range where 0 is most recent
    max_recency = 30 * 24 * 60 * 60  # 30 days in seconds
    scanned_score = min(scanned_recency / max_recency, 1.0)
    seen_score = min(seen_recency / max_recency, 1.0)

    # Freshness is inverse of recency (1 - score)
    return 1.0 - (0.5 * scanned_score + 0.5 * seen_score)

def get_server_freshness(server_id: str, db: Session = Depends(get_session)) -> ServerFreshnessResponse:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    freshness_score = calculate_freshness_score(server.last_scanned, server.last_seen)

    return ServerFreshnessResponse(
        server_id=server.server_id,
        freshness_score=freshness_score,
        last_scanned=server.last_scanned,
        last_seen=server.last_seen
    )

app = FastAPI()

@app.get("/api/servers/{server_id}/freshness", response_model=ServerFreshnessResponse)
async def server_freshness(server_id: str, db: Session = Depends(get_session)):
    return get_server_freshness(server_id, db)

if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    McpServerRegistry.__table__.create(test_engine)

    # Override dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as db:
        recent_server = McpServerRegistry(
            server_id="recent-server",
            last_scanned=datetime.utcnow() - timedelta(hours=1),
            last_seen=datetime.utcnow() - timedelta(hours=1),
            name="Recent Server",
            url="http://recent.example.com",
            confidence=0.9,
            risk_tier="low",
            scan_count=10,
            verdict="safe",
            verdict_reasoning="Recent scan shows no issues"
        )
        stale_server = McpServerRegistry(
            server_id="stale-server",
            last_scanned=datetime.utcnow() - timedelta(days=30),
            last_seen=datetime.utcnow() - timedelta(days=30),
            name="Stale Server",
            url="http://stale.example.com",
            confidence=0.5,
            risk_tier="high",
            scan_count=1,
            verdict="unknown",
            verdict_reasoning="No recent scans"
        )
        db.add_all([recent_server, stale_server])
        db.commit()

    # Test client
    client = TestClient(app)

    # Test endpoints
    recent_response = client.get("/api/servers/recent-server/freshness")
    stale_response = client.get("/api/servers/stale-server/freshness")

    assert recent_response.status_code == 200
    assert stale_response.status_code == 200

    recent_data = recent_response.json()
    stale_data = stale_response.json()

    assert recent_data["freshness_score"] > stale_data["freshness_score"]
    print("PASS")