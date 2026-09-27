from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/scoring/backlog/health")

class HealthMetrics(BaseModel):
    backlog_size: int
    avg_age: Optional[float]

class HealthResponse(BaseModel):
    health: HealthMetrics

def get_unscheduled_servers(session: Session) -> List[McpServerRegistry]:
    return session.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier == "unscheduled"
    ).all()

def calculate_health_metrics(servers: List[McpServerRegistry]) -> HealthMetrics:
    if not servers:
        return HealthMetrics(backlog_size=0, avg_age=None)

    total_age = 0
    now = datetime.utcnow()

    for server in servers:
        if server.last_seen:
            age = (now - server.last_seen).days
            total_age += age

    avg_age = total_age / len(servers) if servers else None
    return HealthMetrics(backlog_size=len(servers), avg_age=avg_age)

@router.get("/", response_model=HealthResponse)
async def get_backlog_health(session: Session = Depends(get_session)):
    unscheduled_servers = get_unscheduled_servers(session)
    metrics = calculate_health_metrics(unscheduled_servers)
    return HealthResponse(health=metrics)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    from app.models import McpServerRegistry
    from datetime import datetime, timedelta

    with TestSessionLocal() as session:
        # Seed 100 servers with 20 unscheduled
        for i in range(100):
            server = McpServerRegistry(
                server_id=f"server_{i}",
                name=f"Server {i}",
                last_seen=datetime.utcnow() - timedelta(days=i % 30),
                risk_tier="unscheduled" if i < 20 else "scheduled",
                verdict="unknown",
                confidence=0.5,
                trust_score=0.5,
                registry_source="test"
            )
            session.add(server)
        session.commit()

    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    response = client.get("/api/scoring/backlog/health")
    assert response.status_code == 200
    data = response.json()
    assert data["health"]["backlog_size"] == 20
    assert data["health"]["avg_age"] is not None

    print("PASS")