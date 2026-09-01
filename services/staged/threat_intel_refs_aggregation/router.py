from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from .logic import get_threat_intel_refs_aggregation

router = APIRouter(prefix="/api/threat-intel")

class Pulse(BaseModel):
    id: int
    name: str
    created: str
    source: str

class IndicatorType(BaseModel):
    type: str
    count: int
    pulses: List[Pulse]

class ThreatIntelResponse(BaseModel):
    indicator_types: List[IndicatorType]
    total_refs: int
    last_fetched: Optional[str]

@router.get("/refs", response_model=ThreatIntelResponse)
async def get_threat_intel_refs(session: Session = Depends(get_session)):
    return get_threat_intel_refs_aggregation(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Seed test data
    with SessionLocal() as session:
        from app.models import ThreatIntelRef, VulnLink
        session.execute("""
            INSERT INTO threat_intel_refs (id, indicator_type, advisory_id, indicator_value, created_at)
            VALUES
                (1, 'ip', 101, '192.168.1.1', '2023-01-01'),
                (2, 'ip', 102, '192.168.1.2', '2023-01-02'),
                (3, 'domain', 101, 'example.com', '2023-01-03'),
                (4, 'domain', 103, 'test.com', '2023-01-04'),
                (5, 'ip', 103, '192.168.1.3', '2023-01-05')
        """)
        session.execute("""
            INSERT INTO vuln_links (id, advisory_id, name, created_at, source)
            VALUES
                (1, 101, 'Pulse 1', '2023-01-01', 'Source A'),
                (2, 102, 'Pulse 2', '2023-01-02', 'Source B'),
                (3, 103, 'Pulse 3', '2023-01-03', 'Source A')
        """)
        session.commit()

    response = client.get("/api/threat-intel/refs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["indicator_types"]) == 2
    assert any(item["type"] == "ip" and item["count"] == 3 for item in data["indicator_types"])
    print("PASS")