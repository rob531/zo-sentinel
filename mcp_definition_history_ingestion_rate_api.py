from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpDefinitionHistory
from pydantic import BaseModel

router = APIRouter()

class IngestionRateResponse(BaseModel):
    rate: float
    last_ingested: str

@router.get("/definition-history/ingestion-rate", response_model=IngestionRateResponse)
def get_ingestion_rate(session: Session = Depends(get_session)):
    latest_record = session.query(McpDefinitionHistory).order_by(McpDefinitionHistory.ingested_at.desc()).first()
    if not latest_record:
        return {"rate": 0.0, "last_ingested": "1970-01-01T00:00:00Z"}

    total_records = session.query(McpDefinitionHistory).count()
    if total_records < 2:
        return {"rate": 0.0, "last_ingested": latest_record.ingested_at.isoformat()}

    oldest_record = session.query(McpDefinitionHistory).order_by(McpDefinitionHistory.ingested_at.asc()).first()
    time_diff = latest_record.ingested_at - oldest_record.ingested_at
    rate = total_records / time_diff.total_seconds() if time_diff.total_seconds() > 0 else 0.0

    return {"rate": rate, "last_ingested": latest_record.ingested_at.isoformat()}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timedelta

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    now = datetime.utcnow()
    for i in range(10):
        db.add(McpDefinitionHistory(ingested_at=now - timedelta(seconds=i)))
    db.commit()
    db.close()

    response = client.get("/definition-history/ingestion-rate")
    assert response.status_code == 200
    assert "rate" in response.json()
    assert "last_ingested" in response.json()
    print("PASS")