from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import ThreatIntelRef
from datetime import datetime
from typing import List, Optional
import json

app = FastAPI()

@app.get("/api/threat_intel_refs")
async def get_threat_intel_refs(
    indicator_type: str = Query(..., description="Type of the indicator"),
    indicator_value: str = Query(..., description="Value of the indicator"),
    session: Session = Depends(get_session)
) -> dict:
    """
    Retrieve threat intelligence reference records for a given indicator type and value.

    Args:
        indicator_type: Type of the indicator (e.g., IP, Domain, URL)
        indicator_value: Value of the indicator (e.g., 1.2.3.4, example.com)
        session: SQLAlchemy database session

    Returns:
        JSON object with threat intelligence reference records
    """
    records = session.query(ThreatIntelRef).filter(
        ThreatIntelRef.indicator_type == indicator_type,
        ThreatIntelRef.indicator_value == indicator_value
    ).all()

    result = {
        "records": [
            {
                "id": record.id,
                "pulse_id": record.pulse_id,
                "pulse_name": record.pulse_name,
                "source": record.source,
                "source_url": record.source_url,
                "fetched_at": record.fetched_at.isoformat()
            } for record in records
        ]
    }

    return result

def create_test_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return SessionLocal()

def insert_test_data(session):
    test_data = [
        {
            "indicator_type": "IP",
            "indicator_value": "1.2.3.4",
            "pulse_id": "pulse1",
            "pulse_name": "Test Pulse 1",
            "pulse_created": datetime.now(),
            "is_aggregator": False,
            "source": "Source 1",
            "source_url": "http://example.com/source1",
            "fetched_at": datetime.now()
        },
        {
            "indicator_type": "IP",
            "indicator_value": "1.2.3.4",
            "pulse_id": "pulse2",
            "pulse_name": "Test Pulse 2",
            "pulse_created": datetime.now(),
            "is_aggregator": True,
            "source": "Source 2",
            "source_url": "http://example.com/source2",
            "fetched_at": datetime.now()
        }
    ]

    for data in test_data:
        record = ThreatIntelRef(**data)
        session.add(record)
    session.commit()

if __name__ == "__main__":
    from fastapi import Depends
    from sqlalchemy.orm import Session

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: create_test_db()

    # Create test database and insert test data
    test_session = create_test_db()
    insert_test_data(test_session)

    # Start the test client
    client = TestClient(app)

    # Make a test request
    response = client.get("/api/threat_intel_refs?indicator_type=IP&indicator_value=1.2.3.4")

    # Assert the response
    assert response.status_code == 200
    response_data = response.json()
    assert len(response_data["records"]) == 2
    assert response_data["records"][0]["pulse_id"] == "pulse1"
    assert response_data["records"][0]["source"] == "Source 1"

    print("PASS")