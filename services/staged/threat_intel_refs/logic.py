from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from app.db import get_session
from app.models import ThreatIntelRef

class ThreatIntelRefResponse(BaseModel):
    id: int
    pulse_id: str
    pulse_name: str
    source: str
    source_url: str
    fetched_at: str

class ThreatIntelRefsResponse(BaseModel):
    records: List[ThreatIntelRefResponse]

def get_threat_intel_refs(
    indicator_type: str,
    indicator_value: str,
    session: Session = Depends(get_session)
) -> ThreatIntelRefsResponse:
    records = session.query(ThreatIntelRef).filter(
        ThreatIntelRef.indicator_type == indicator_type,
        ThreatIntelRef.indicator_value == indicator_value
    ).all()

    response_records = []
    for record in records:
        response_records.append({
            "id": record.id,
            "pulse_id": record.pulse_id,
            "pulse_name": record.pulse_name,
            "source": record.source,
            "source_url": record.source_url,
            "fetched_at": record.fetched_at.isoformat()
        })

    return ThreatIntelRefsResponse(records=response_records)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    with SessionLocal() as session:
        session.add_all([
            ThreatIntelRef(
                id=1,
                indicator_type="IP",
                indicator_value="1.2.3.4",
                pulse_id="pulse1",
                pulse_name="Test Pulse 1",
                pulse_created=datetime.now(),
                is_aggregator=False,
                source="Test Source 1",
                source_url="http://test1.com",
                fetched_at=datetime.now()
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="IP",
                indicator_value="1.2.3.4",
                pulse_id="pulse2",
                pulse_name="Test Pulse 2",
                pulse_created=datetime.now(),
                is_aggregator=False,
                source="Test Source 2",
                source_url="http://test2.com",
                fetched_at=datetime.now()
            )
        ])
        session.commit()

    response = client.get("/api/threat_intel_refs?indicator_type=IP&indicator_value=1.2.3.4")
    assert response.status_code == 200
    data = response.json()
    assert len(data["records"]) == 2
    assert data["records"][0]["id"] == 1
    assert data["records"][0]["pulse_id"] == "pulse1"
    assert data["records"][0]["pulse_name"] == "Test Pulse 1"
    assert data["records"][0]["source"] == "Test Source 1"
    assert data["records"][0]["source_url"] == "http://test1.com"

    print("PASS")