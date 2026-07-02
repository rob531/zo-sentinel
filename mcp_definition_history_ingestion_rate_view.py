from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import mcp_definition_history
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class IngestionRateResponse(BaseModel):
    ingestion_rate: float
    last_ingested: datetime

@router.get("/definition-history-ingestion-rate", response_model=IngestionRateResponse)
def get_definition_history_ingestion_rate(session: Session = Depends(get_session)):
    result = session.query(
        func.count(mcp_definition_history.id).label('count'),
        func.max(mcp_definition_history.ingested_at).label('last_ingested')
    ).first()

    if result.count == 0:
        return IngestionRateResponse(ingestion_rate=0.0, last_ingested=None)

    ingestion_rate = result.count / 3600  # Assuming 1 hour window
    last_ingested = result.last_ingested

    return IngestionRateResponse(ingestion_rate=ingestion_rate, last_ingested=last_ingested)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Column, Integer, DateTime
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    class TestMcpDefinitionHistory(Base):
        __tablename__ = "mcp_definition_history"
        id = Column(Integer, primary_key=True, index=True)
        ingested_at = Column(DateTime)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)
    app.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    test_data = [
        TestMcpDefinitionHistory(ingested_at=datetime.now()),
        TestMcpDefinitionHistory(ingested_at=datetime.now())
    ]
    db.add_all(test_data)
    db.commit()
    db.close()

    response = client.get("/definition-history-ingestion-rate")
    assert response.status_code == 200
    assert "ingestion_rate" in response.json()
    assert "last_ingested" in response.json()
    print("PASS")