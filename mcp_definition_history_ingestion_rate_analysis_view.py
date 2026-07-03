from fastapi import APIRouter, Depends
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from app.db import get_session
from app.models import MCPDefinitionHistory
from sqlalchemy import func
from fastapi.testclient import TestClient
import sqlalchemy as sa

router = APIRouter()

class IngestionRateResponse(BaseModel):
    ingestion_rate: float
    last_updated: str

@router.get("/mcp-definition-history/ingestion-rate", response_model=IngestionRateResponse)
async def get_ingestion_rate(db_session=Depends(get_session)):
    # Calculate the ingestion rate as the count of records divided by the time span
    subquery = (
        db_session.query(
            func.count(MCPDefinitionHistory.id).label("count"),
            func.min(MCPDefinitionHistory.created_at).label("min_created_at"),
            func.max(MCPDefinitionHistory.created_at).label("max_created_at")
        )
        .subquery()
    )

    result = (
        db_session.query(
            (subquery.c.count / sa.extract('epoch', subquery.c.max_created_at - subquery.c.min_created_at)).label("ingestion_rate"),
            subquery.c.max_created_at.label("last_updated")
        )
        .first()
    )

    if result is None:
        return {"ingestion_rate": 0.0, "last_updated": datetime.utcnow().isoformat()}

    ingestion_rate = result.ingestion_rate if result.ingestion_rate is not None else 0.0
    last_updated = result.last_updated.isoformat() if result.last_updated else datetime.utcnow().isoformat()

    return {"ingestion_rate": ingestion_rate, "last_updated": last_updated}

if __name__ == "__main__":
    from app.db import Base, engine
    from app.models import MCPDefinitionHistory

    # Override the session for testing
    from sqlalchemy.orm import sessionmaker
    test_engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    from app import app
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        from datetime import datetime, timedelta
        session.add_all([
            MCPDefinitionHistory(created_at=datetime.utcnow() - timedelta(days=2)),
            MCPDefinitionHistory(created_at=datetime.utcnow() - timedelta(days=1)),
            MCPDefinitionHistory(created_at=datetime.utcnow())
        ])
        session.commit()

    client = TestClient(app)

    response = client.get("/mcp-definition-history/ingestion-rate")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["ingestion_rate"], float)
    assert isinstance(data["last_updated"], str)

    print("PASS")