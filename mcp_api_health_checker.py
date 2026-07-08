from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class HealthCheckResponse(BaseModel):
    mcp_scanner: datetime
    signal_analyser: datetime
    trust_synthesiser: datetime
    attestation_engine: datetime

@router.get("/health/check", response_model=HealthCheckResponse)
async def health_check(db: Session = Depends(get_session)) -> HealthCheckResponse:
    services = ['mcp_scanner', 'signal_analyser', 'trust_synthesiser', 'attestation_engine']
    now = datetime.utcnow()
    twenty_four_hours_ago = now - timedelta(hours=24)

    health_data = db.query(
        ServiceHealth.service_name,
        func.max(ServiceHealth.timestamp).label('last_heartbeat')
    ).filter(
        ServiceHealth.service_name.in_(services),
        ServiceHealth.timestamp >= twenty_four_hours_ago
    ).group_by(
        ServiceHealth.service_name
    ).all()

    if len(health_data) != 4:
        raise HTTPException(status_code=500, detail="Not all services have recent heartbeats")

    return HealthCheckResponse(
        mcp_scanner=health_data[0][1],
        signal_analyser=health_data[1][1],
        trust_synthesiser=health_data[2][1],
        attestation_engine=health_data[3][1]
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker
    from app.models import ServiceHealth

    # Override the dependency for testing
    from app import app
    from app.db import get_session

    # Create a test session
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_data = [
        ServiceHealth(service_name="mcp_scanner", timestamp=datetime.utcnow() - timedelta(hours=1)),
        ServiceHealth(service_name="signal_analyser", timestamp=datetime.utcnow() - timedelta(hours=2)),
        ServiceHealth(service_name="trust_synthesiser", timestamp=datetime.utcnow() - timedelta(hours=3)),
        ServiceHealth(service_name="attestation_engine", timestamp=datetime.utcnow() - timedelta(hours=4))
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/health/check")
    assert response.status_code == 200
    assert response.json()["mcp_scanner"] is not None
    assert response.json()["signal_analyser"] is not None
    assert response.json()["trust_synthesiser"] is not None
    assert response.json()["attestation_engine"] is not None

    print("PASS")