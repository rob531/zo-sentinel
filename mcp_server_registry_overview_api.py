# deps: fastapi, pydantic, sqlalchemy, sqlmodel
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, JSON, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import List, Dict, Optional

# Import existing models from app.models
from app.models import McpServerRegistry, get_session

# Define Pydantic models for request and response
class ServerOverview(BaseModel):
    total_servers: int
    registry_source_counts: Dict[str, int]
    risk_tier_counts: Dict[str, int]

class ServerOverviewResponse(BaseModel):
    overview: ServerOverview

# Create FastAPI router
router = APIRouter()

# Dependency to get DB session
@router.get("/overview", response_model=ServerOverviewResponse)
def get_server_overview(db: Session = Depends(get_session)):
    try:
        # Query total number of servers
        total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar()

        # Query count by registry source
        registry_source_counts = db.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id)
        ).group_by(McpServerRegistry.registry_source).all()

        # Query count by risk tier
        risk_tier_counts = db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id)
        ).group_by(McpServerRegistry.risk_tier).all()

        # Convert query results to dictionaries
        registry_source_counts_dict = {source: count for source, count in registry_source_counts}
        risk_tier_counts_dict = {tier: count for tier, count in risk_tier_counts}

        # Create response object
        overview = ServerOverview(
            total_servers=total_servers,
            registry_source_counts=registry_source_counts_dict,
            risk_tier_counts=risk_tier_counts_dict
        )

        return ServerOverviewResponse(overview=overview)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Self-test with FastAPI TestClient
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a SQLite in-memory database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Override get_session dependency for testing
    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_db

    # Seed test data
    def seed_test_data(db: Session):
        test_data = [
            McpServerRegistry(server_id=1, name="Server1", registry_source="Source1", risk_tier="Low"),
            McpServerRegistry(server_id=2, name="Server2", registry_source="Source2", risk_tier="Medium"),
            McpServerRegistry(server_id=3, name="Server3", registry_source="Source1", risk_tier="High"),
        ]
        db.add_all(test_data)
        db.commit()

    # Create TestClient
    client = TestClient(app)

    # Seed test data
    with TestingSessionLocal() as db:
        seed_test_data(db)

    # Test the /overview endpoint
    response = client.get("/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["overview"]["total_servers"] == 3
    assert data["overview"]["registry_source_counts"] == {"Source1": 2, "Source2": 1}
    assert data["overview"]["risk_tier_counts"] == {"Low": 1, "Medium": 1, "High": 1}
    print("PASS")