from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_health_matrix as _get_health_matrix
except ImportError:
    from logic import get_health_matrix as _get_health_matrix


router = APIRouter(prefix="/api", tags=["daemon-health"])


@router.get("/daemons/health-matrix")
def daemon_health_matrix(session: Session = Depends(get_session)):
    return _get_health_matrix(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT NOT NULL,
                    last_heartbeat DATETIME NOT NULL,
                    status TEXT NOT NULL,
                    meta JSON
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "fresh-service",
                    "last_heartbeat": now,
                    "status": "healthy",
                    "meta": "{}",
                },
                {
                    "service": "stale-service",
                    "last_heartbeat": now - timedelta(minutes=10),
                    "status": "degraded",
                    "meta": "{}",
                },
                {
                    "service": "very-stale-service",
                    "last_heartbeat": now - timedelta(hours=2),
                    "status": "unhealthy",
                    "meta": "{}",
                },
            ],
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/daemons/health-matrix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3
        assert payload["by_class"] == {"ok": 1, "stale": 1, "very_stale": 1}
        assert payload["services"][0]["service"] == "very-stale-service"

    print("PASS")