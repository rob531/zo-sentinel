from fastapi import APIRouter, Depends
from datetime import datetime
from sqlalchemy import func
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPDefinitionHistory
from fastapi.testclient import TestClient

router = APIRouter()

class IngestionRateResponse(BaseModel):
    ingestion_rate: float
    last_updated: datetime

@router.get("/mcp-definition-history/ingestion-rate", response_model=IngestionRateResponse)
async def get_ingestion_rate(db_session=Depends(get_session)):
    result = db_session.query(
        func.count(MCPDefinitionHistory.id).label("count"),
        func.max(MCPDefinitionHistory.created_at).label("last_updated")
    ).first()

    if result is None:
        return {"ingestion_rate": 0.0, "last_updated": datetime.min}

    count = result.count
    last_updated = result.last_updated

    # Calculate ingestion rate (records per second)
    if last_updated is not None:
        time_elapsed = (last_updated - datetime.min).total_seconds()
        ingestion_rate = count / time_elapsed if time_elapsed > 0 else 0.0
    else:
        ingestion_rate = 0.0

    return {"ingestion_rate": ingestion_rate, "last_updated": last_updated}

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        from datetime import datetime, timedelta
        session.add_all([
            MCPDefinitionHistory(created_at=datetime.now() - timedelta(hours=1)),
            MCPDefinitionHistory(created_at=datetime.now() - timedelta(minutes=30)),
            MCPDefinitionHistory(created_at=datetime.now())
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/mcp-definition-history/ingestion-rate")
    assert response.status_code == 200
    assert "ingestion_rate" in response.json()
    assert "last_updated" in response.json()
    print("PASS")