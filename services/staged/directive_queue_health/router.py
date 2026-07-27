from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth
from sqlalchemy.orm import Session
import requests
from .logic import get_directive_queue_health

router = APIRouter()

class QueueHealthResponse(BaseModel):
    total: int
    pending_count: int
    proposed_count: int
    oldest_pending_age_seconds: Optional[float]
    oldest_proposed_age_seconds: Optional[float]
    queue_depth: int

@router.get("/api/directives/queue-health", response_model=QueueHealthResponse)
async def get_queue_health(session: Session = Depends(get_session)):
    try:
        health_data = get_directive_queue_health(session)
        return health_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Mock write_service responses
    def mock_write_service_query(endpoint: str, params: dict):
        if endpoint == "/query":
            if params.get("query") == "read_pending_directives":
                return {
                    "pending": [
                        {"filename": "dir1", "timestamp": "2023-01-01T00:00:00Z"},
                        {"filename": "dir2", "timestamp": "2023-01-02T00:00:00Z"}
                    ],
                    "proposed": [
                        {"filename": "dir3", "timestamp": "2023-01-03T00:00:00Z"}
                    ]
                }
        return {}

    original_post = requests.post

    def mock_post(*args, **kwargs):
        if "http://127.0.0.1:8772/query" in args[0]:
            return mock_write_service_query(*args, **kwargs)
        return original_post(*args, **kwargs)

    requests.post = mock_post

    client = TestClient(app)

    response = client.get("/api/directives/queue-health")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 0
    assert isinstance(data["oldest_pending_age_seconds"], (float, type(None)))
    assert isinstance(data["oldest_proposed_age_seconds"], (float, type(None)))
    print("PASS")