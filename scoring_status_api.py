from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class PipelineHealth(str, Enum):
    healthy = "healthy"
    stale = "stale"
    degraded = "degraded"

def get_recently_scored_servers(db: Session) -> int:
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    return db.query(MCPServerRegistry).filter(
        MCPServerRegistry.last_scanned >= seven_days_ago
    ).count()

def get_total_servers(db: Session) -> int:
    return db.query(func.count(MCPServerRegistry.id)).scalar()

def calculate_pipeline_health(scored: int, total: int) -> PipelineHealth:
    if total == 0:
        return PipelineHealth.degraded
    ratio = scored / total
    if ratio > 0.8:
        return PipelineHealth.healthy
    elif ratio > 0.5:
        return PipelineHealth.stale
    else:
        return PipelineHealth.degraded

@router.get("/scoring/status")
async def get_scoring_status(db: Session = Depends(get_session)) -> dict:
    total_servers = get_total_servers(db)
    scored_servers = get_recently_scored_servers(db)
    pipeline_health = calculate_pipeline_health(scored_servers, total_servers)

    return {
        "total_servers": total_servers,
        "scored_servers": scored_servers,
        "pipeline_health": pipeline_health,
        "checked_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import get_session, SessionLocal

    # Mock database session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/scoring/status")
    assert response.status_code == 200
    data = response.json()
    assert "total_servers" in data
    assert "scored_servers" in data
    assert "pipeline_health" in data
    assert data["pipeline_health"] in [health.value for health in PipelineHealth]
    assert "checked_at" in data

    print("PASS")