from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import ServiceHealth

router = APIRouter()

class DaemonHealth(BaseModel):
    name: str
    last_heartbeat: Optional[datetime]
    status: Optional[str]
    lag_seconds: Optional[float]

class HealthResponse(BaseModel):
    daemons: List[DaemonHealth]
    overall_status: str
    checked_at: datetime

def get_daemon_health(session) -> List[DaemonHealth]:
    daemon_names = ['signal_analyser', 'trust_synthesiser', 'risk_ranker', 'data_velocity']
    daemons = []

    for name in daemon_names:
        health = session.query(ServiceHealth).filter(ServiceHealth.service_name == name).first()
        if health:
            lag_seconds = (datetime.utcnow() - health.last_heartbeat).total_seconds()
            daemons.append(DaemonHealth(
                name=name,
                last_heartbeat=health.last_heartbeat,
                status=health.status,
                lag_seconds=lag_seconds
            ))
        else:
            daemons.append(DaemonHealth(
                name=name,
                last_heartbeat=None,
                status=None,
                lag_seconds=None
            ))

    return daemons

def compute_overall_status(daemons: List[DaemonHealth]) -> str:
    critical_count = 0
    degraded_count = 0

    for daemon in daemons:
        if daemon.status == 'critical':
            critical_count += 1
        elif daemon.status == 'degraded':
            degraded_count += 1

    if critical_count > 0:
        return 'critical'
    elif degraded_count > 0:
        return 'degraded'
    else:
        return 'ok'

@router.get("/health/scoring-pipeline", response_model=HealthResponse)
async def get_scoring_pipeline_health(session=Depends(get_session)):
    daemons = get_daemon_health(session)
    overall_status = compute_overall_status(daemons)

    return {
        "daemons": daemons,
        "overall_status": overall_status,
        "checked_at": datetime.utcnow()
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    test_session = TestSession()
    test_session.add_all([
        ServiceHealth(
            service_name="signal_analyser",
            last_heartbeat=datetime.utcnow(),
            status="ok"
        ),
        ServiceHealth(
            service_name="trust_synthesiser",
            last_heartbeat=datetime.utcnow(),
            status="ok"
        )
    ])
    test_session.commit()

    client = TestClient(app)

    response = client.get("/health/scoring-pipeline")
    assert response.status_code == 200
    assert response.json()["overall_status"] == "ok"
    assert any(daemon["name"] == "signal_analyser" for daemon in response.json()["daemons"])

    print("PASS")