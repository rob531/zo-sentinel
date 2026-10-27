from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_heartbeat_lag

router = APIRouter(prefix="/api/service-health", tags=["service-health"])


@router.get("/heartbeat-lag")
def heartbeat_lag(session: Session = Depends(get_session)):
    return get_heartbeat_lag(session)


def _self_test() -> None:
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
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_heartbeat DATETIME NOT NULL,
                    meta JSON
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO service_health (service, status, last_heartbeat, meta)
                VALUES (:service, :status, :last_heartbeat, :meta)
                """
            ),
            [
                {
                    "service": "healthy-service",
                    "status": "healthy",
                    "last_heartbeat": now - timedelta(seconds=60),
                    "meta": "{}",
                },
                {
                    "service": "stale-service",
                    "status": "degraded",
                    "last_heartbeat": now - timedelta(seconds=900),
                    "meta": "{}",
                },
                {
                    "service": "dead-service",
                    "status": "down",
                    "last_heartbeat": now - timedelta(seconds=7200),
                    "meta": "{}",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/service-health/heartbeat-lag")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["services"]) == 3
    assert payload["band_counts"]["ok"] == 1
    assert payload["band_counts"]["dead"] == 1

    lags = [service["lag_seconds"] for service in payload["services"]]
    assert lags == sorted(lags, reverse=True)
    assert sum(service["band"] == "ok" for service in payload["services"]) == 1
    assert sum(service["band"] == "dead" for service in payload["services"]) == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()