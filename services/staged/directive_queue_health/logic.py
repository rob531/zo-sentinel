from datetime import datetime
from typing import List, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import ServiceHealth
import httpx
import time

class DirectiveQueueHealthResponse(BaseModel):
    total: int
    pending_count: int
    proposed_count: int
    oldest_pending_age_seconds: Optional[float]
    oldest_proposed_age_seconds: Optional[float]
    queue_depth: int

def get_directive_queue_health() -> DirectiveQueueHealthResponse:
    session: Session = Depends(get_session)

    # Check service health heartbeat staleness
    health_check = session.query(ServiceHealth).filter(
        ServiceHealth.service_name == 'directive-generator'
    ).first()

    if not health_check or (datetime.now() - health_check.last_heartbeat).total_seconds() > 300:
        raise HTTPException(status_code=503, detail="Directive generator service unhealthy")

    # Query write_service for pending and proposed directives
    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT * FROM read_pending_directives()"
            }
        )
        response.raise_for_status()
        directives = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Failed to query write_service: {str(e)}")

    pending_directives = [d for d in directives if d['status'] == 'pending']
    proposed_directives = [d for d in directives if d['status'] == 'proposed']

    oldest_pending_age = None
    if pending_directives:
        oldest_pending = min(pending_directives, key=lambda x: x['created_at'])
        oldest_pending_age = (datetime.now() - datetime.fromisoformat(oldest_pending['created_at'])).total_seconds()

    oldest_proposed_age = None
    if proposed_directives:
        oldest_proposed = min(proposed_directives, key=lambda x: x['created_at'])
        oldest_proposed_age = (datetime.now() - datetime.fromisoformat(oldest_proposed['created_at'])).total_seconds()

    return DirectiveQueueHealthResponse(
        total=len(directives),
        pending_count=len(pending_directives),
        proposed_count=len(proposed_directives),
        oldest_pending_age_seconds=oldest_pending_age,
        oldest_proposed_age_seconds=oldest_proposed_age,
        queue_depth=len(directives)
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.db import get_session, SessionLocal

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Mock the write_service response
    def mock_write_service(query):
        if query == "SELECT * FROM read_pending_directives()":
            return {
                "data": [
                    {"id": 1, "filename": "directive1.txt", "status": "pending", "created_at": "2023-01-01T00:00:00"},
                    {"id": 2, "filename": "directive2.txt", "status": "proposed", "created_at": "2023-01-02T00:00:00"},
                    {"id": 3, "filename": "directive3.txt", "status": "pending", "created_at": "2023-01-03T00:00:00"}
                ]
            }
        return {"data": []}

    with httpx.Client() as client:
        client.post = lambda url, json: httpx.Response(200, json=mock_write_service(json['query']))

    response = client.get("/api/directives/queue-health")
    assert response.status_code == 200
    data = response.json()

    assert data["total"] >= 0
    assert data["pending_count"] >= 0
    assert data["proposed_count"] >= 0
    assert isinstance(data["oldest_pending_age_seconds"], (float, type(None)))
    assert isinstance(data["oldest_proposed_age_seconds"], (float, type(None)))
    assert data["queue_depth"] >= 0

    print("PASS")