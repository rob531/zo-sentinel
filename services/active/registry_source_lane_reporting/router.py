# deps: fastapi, pydantic, sqlalchemy, sqlmodel, passlib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, Org, User, ApiKey
from app.auth import require_role

router = APIRouter()

class RegistrySourceLaneReport(BaseModel):
    registry_source: str
    lane_count: int

class RegistrySourceLaneReportRequest(BaseModel):
    org_id: int

@router.get("/registry-source-lane-report", response_model=List[RegistrySourceLaneReport])
async def get_registry_source_lane_report(
    org_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(require_role("admin"))
):
    # Query the database for registry source lane reports scoped by org_id
    reports = db.query(
        McpServerRegistry.registry_source,
        func.count(McpServerRegistry.registry_source).label("lane_count")
    ).filter(McpServerRegistry.org_id == org_id).group_by(McpServerRegistry.registry_source).all()
    
    return [RegistrySourceLaneReport(registry_source=report.registry_source, lane_count=report.lane_count) for report in reports]

if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the get_session dependency for testing
    def override_get_session():
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create a test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/registry-source-lane-report?org_id=1")
    assert response.status_code == 200
    print("PASS")