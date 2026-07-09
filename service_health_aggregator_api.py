from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ServiceHealth

router = APIRouter()

class ServiceHealthResponse(BaseModel):
    name: str
    status: str
    last_heartbeat: datetime
    meta: Optional[dict]
    age_seconds: float

class HealthSummary(BaseModel):
    total: int
    healthy_count: int
    stale_count: int
    stale_services: List[str]

class AggregatedHealthResponse(BaseModel):
    services: List[ServiceHealthResponse]
    summary: HealthSummary

def get_service_health(session: Session = Depends(get_session)) -> List[ServiceHealth]:
    return session.query(ServiceHealth).all()

@router.get("/health/aggregated", response_model=AggregatedHealthResponse)
async def get_aggregated_health(session: Session = Depends(get_session)):
    services = get_service_health(session)
    now = datetime.utcnow()
    response_services = []
    stale_services = []
    healthy_count = 0
    stale_count = 0

    for service in services:
        age_seconds = (now - service.last_heartbeat).total_seconds()
        is_stale = age_seconds > 300

        if is_stale:
            stale_count += 1
            stale_services.append(service.name)
        elif service.status == "healthy":
            healthy_count += 1

        response_services.append(
            ServiceHealthResponse(
                name=service.name,
                status=service.status,
                last_heartbeat=service.last_heartbeat,
                meta=service.meta,
                age_seconds=age_seconds,
            )
        )

    return AggregatedHealthResponse(
        services=response_services,
        summary=HealthSummary(
            total=len(services),
            healthy_count=healthy_count,
            stale_count=stale_count,
            stale_services=stale_services,
        ),
    )

@router.get("/health/service/{service_name}", response_model=ServiceHealthResponse)
async def get_service_health_by_name(service_name: str, session: Session = Depends(get_session)):
    services = get_service_health(session)
    for service in services:
        if service.name == service_name:
            now = datetime.utcnow()
            age_seconds = (now - service.last_heartbeat).total_seconds()
            return ServiceHealthResponse(
                name=service.name,
                status=service.status,
                last_heartbeat=service.last_heartbeat,
                meta=service.meta,
                age_seconds=age_seconds,
            )
    raise ValueError(f"Service {service_name} not found")

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables and seed data
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    session.add_all([
        ServiceHealth(
            name="healthy_service",
            status="healthy",
            last_heartbeat=datetime.utcnow(),
            meta={"version": "1.0"}
        ),
        ServiceHealth(
            name="stale_service",
            status="healthy",
            last_heartbeat=datetime.utcnow() - timedelta(seconds=301),
            meta={"version": "1.0"}
        ),
        ServiceHealth(
            name="unknown_service",
            status="unknown",
            last_heartbeat=datetime.utcnow() - timedelta(seconds=10),
            meta={"version": "1.0"}
        )
    ])
    session.commit()

    client = TestClient(app)
    response = client.get("/health/aggregated")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total"] == 3
    assert data["summary"]["healthy_count"] == 1
    assert data["summary"]["stale_count"] == 1
    assert data["summary"]["stale_services"] == ["stale_service"]

    print("PASS")