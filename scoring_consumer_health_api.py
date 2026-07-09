from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from datetime import datetime, timedelta
from app.db import get_session
from app.models import ServiceHealth, McpLlmAxisScores

router = APIRouter()

class HealthStatus(BaseModel):
    service: str
    status: str
    last_heartbeat: datetime
    age_seconds: float
    is_stale: bool
    scored_at: datetime | None

@router.get("/scoring/consumer/status", response_model=HealthStatus)
async def get_consumer_health(db=Depends(get_session)):
    # Check service health
    stmt = select(ServiceHealth).where(ServiceHealth.service == 'app_scoring_consumer')
    health = db.execute(stmt).scalar_one_or_none()

    if not health:
        return HealthStatus(
            service='app_scoring_consumer',
            status='down',
            last_heartbeat=None,
            age_seconds=None,
            is_stale=True,
            scored_at=None
        )

    age_seconds = (datetime.utcnow() - health.last_heartbeat).total_seconds()
    is_stale = age_seconds > 120

    # Get most recent scored_at
    scored_at = db.execute(
        select(McpLlmAxisScores.scored_at)
        .order_by(McpLlmAxisScores.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    return HealthStatus(
        service='app_scoring_consumer',
        status='up' if not is_stale else 'stale',
        last_heartbeat=health.last_heartbeat,
        age_seconds=age_seconds,
        is_stale=is_stale,
        scored_at=scored_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import ServiceHealth, McpLlmAxisScores

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    # Test client
    client = TestClient(test_app)

    # Test cases
    def test_consumer_health():
        # Test with recent heartbeat
        with TestSession() as session:
            session.add(ServiceHealth(
                service='app_scoring_consumer',
                last_heartbeat=datetime.utcnow() - timedelta(seconds=60)
            ))
            session.add(McpLlmAxisScores(scored_at=datetime.utcnow() - timedelta(hours=1)))
            session.commit()

        response = client.get("/scoring/consumer/status")
        assert response.json()["is_stale"] == False
        assert "scored_at" in response.json()

        # Test with stale heartbeat
        with TestSession() as session:
            session.add(ServiceHealth(
                service='app_scoring_consumer',
                last_heartbeat=datetime.utcnow() - timedelta(seconds=180)
            ))
            session.commit()

        response = client.get("/scoring/consumer/status")
        assert response.json()["is_stale"] == True

        print("PASS")

    test_consumer_health()