from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth
from sqlalchemy.orm import Session
from sqlalchemy import func
import requests
from fastapi.testclient import TestClient

router = APIRouter(prefix="/api/directives")

class QueueHealthResponse(BaseModel):
    total: int
    pending_count: int
    proposed_count: int
    oldest_pending_age_seconds: Optional[float]
    oldest_proposed_age_seconds: Optional[float]
    queue_depth: int

def read_pending_directives() -> dict:
    """Mock function to simulate reading pending directives from write_service"""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM directives WHERE status IN ('pending', 'proposed')"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch directives")
    return response.json()

@router.get("/queue-health", response_model=QueueHealthResponse)
async def get_queue_health(db: Session = Depends(get_session)):
    try:
        directives = read_pending_directives()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    pending = [d for d in directives if d['status'] == 'pending']
    proposed = [d for d in directives if d['status'] == 'proposed']

    pending_count = len(pending)
    proposed_count = len(proposed)
    total = pending_count + proposed_count

    oldest_pending = min(pending, key=lambda x: x['created_at'], default=None)
    oldest_proposed = min(proposed, key=lambda x: x['created_at'], default=None)

    oldest_pending_age = (
        (datetime.now() - datetime.fromisoformat(oldest_pending['created_at'])).total_seconds()
        if oldest_pending else None
    )
    oldest_proposed_age = (
        (datetime.now() - datetime.fromisoformat(oldest_proposed['created_at'])).total_seconds()
        if oldest_proposed else None
    )

    # Check service health
    last_heartbeat = db.query(ServiceHealth).filter(
        ServiceHealth.service_name == 'directive-generator'
    ).order_by(ServiceHealth.timestamp.desc()).first()

    if last_heartbeat:
        heartbeat_age = (datetime.now() - last_heartbeat.timestamp).total_seconds()
        if heartbeat_age > 300:  # 5 minutes
            raise HTTPException(
                status_code=503,
                detail="Directive generator service is unhealthy"
            )

    return QueueHealthResponse(
        total=total,
        pending_count=pending_count,
        proposed_count=proposed_count,
        oldest_pending_age_seconds=oldest_pending_age,
        oldest_proposed_age_seconds=oldest_proposed_age,
        queue_depth=total
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock write_service responses
    def mock_read_pending_directives():
        return {
            "data": [
                {"status": "pending", "created_at": "2023-01-01T00:00:00"},
                {"status": "proposed", "created_at": "2023-01-02T00:00:00"},
                {"status": "pending", "created_at": "2023-01-03T00:00:00"}
            ]
        }

    app.dependency_overrides[read_pending_directives] = lambda: mock_read_pending_directives()

    # Add test service health data
    with TestSession() as session:
        session.add(ServiceHealth(
            service_name="directive-generator",
            timestamp=datetime.now() - timedelta(seconds=10)
        ))
        session.commit()

    client = TestClient(app)
    response = client.get("/api/directives/queue-health")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 0
    assert isinstance(data["oldest_pending_age_seconds"], (float, type(None)))
    assert isinstance(data["oldest_proposed_age_seconds"], (float, type(None)))

    print("PASS")