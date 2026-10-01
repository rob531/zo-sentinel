from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/registry_source_freshness_report", tags=["registry_source_freshness_report"])

class RegistrySourceFreshnessReport(BaseModel):
    source: str
    server_count: int
    fresh_count: int
    stale_count: int
    freshness_ratio: float

class FreshnessThreshold(BaseModel):
    days: int

@router.get("/report", response_model=List[RegistrySourceFreshnessReport])
def get_registry_source_freshness_report(
    threshold: FreshnessThreshold,
    db: Session = Depends(get_session)
):
    # Calculate the cutoff date based on the threshold
    cutoff_date = datetime.now() - timedelta(days=threshold.days)

    # Get all unique registry sources
    sources = db.query(McpServerRegistry.registry_source).distinct().all()
    sources = [source[0] for source in sources]

    reports = []

    for source in sources:
        # Get all servers for the current source
        servers = db.query(McpServerRegistry).filter(McpServerRegistry.registry_source == source).all()

        # Count fresh and stale servers
        fresh_count = sum(1 for server in servers if server.last_scanned >= cutoff_date)
        stale_count = len(servers) - fresh_count

        # Calculate freshness ratio
        freshness_ratio = fresh_count / len(servers) if len(servers) > 0 else 0.0

        reports.append(
            RegistrySourceFreshnessReport(
                source=source,
                server_count=len(servers),
                fresh_count=fresh_count,
                stale_count=stale_count,
                freshness_ratio=freshness_ratio
            )
        )

    return reports

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Set up test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Override get_session for testing
    test_app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Test client
    client = TestClient(test_app)

    # Test data
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Add test data
    test_db = TestSessionLocal()
    test_db.add_all([
        McpServerRegistry(
            server_id="1",
            name="test_server_1",
            registry_source="source_1",
            last_scanned=datetime.now()
        ),
        McpServerRegistry(
            server_id="2",
            name="test_server_2",
            registry_source="source_1",
            last_scanned=datetime.now() - timedelta(days=2)
        ),
        McpServerRegistry(
            server_id="3",
            name="test_server_3",
            registry_source="source_2",
            last_scanned=datetime.now() - timedelta(days=3)
        )
    ])
    test_db.commit()

    # Test endpoint
    response = client.get("/api/registry_source_freshness_report/report?threshold_days=1")
    assert response.status_code == 200
    assert len(response.json()) == 2

    print("PASS")