"""
contract for write_service_staleness_probe
"""
from datetime import datetime, timedelta
from typing import List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

app = FastAPI()


class ServiceStalenessResult(BaseModel):
    service: str
    is_stale: bool
    age_seconds: float
    last_heartbeat: str


@app.get("/api/internal/probe/write_service_staleness", response_model=List[ServiceStalenessResult])
async def get_write_service_staleness_probe(session: Session = Depends(get_session)) -> List[ServiceStalenessResult]:
    """
    Check staleness of write_service entries in service_health table.
    Queries service_health via the app db session for write_service entries,
    computes age of last_heartbeat vs current time; if age > 300s the service is stale.
    Returns {service, is_stale, age_seconds, last_heartbeat} for each monitored service.
    """
    now = datetime.utcnow()
    threshold = 300.0

    result = session.execute(
        text("SELECT service_name, last_heartbeat FROM service_health WHERE service_type = 'write_service'")
    )
    rows = result.fetchall()

    services = []
    for row in rows:
        service_name = row[0]
        last_heartbeat_str = row[1]
        last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
        age_seconds = (now - last_heartbeat).total_seconds()
        is_stale = age_seconds > threshold

        services.append(
            ServiceStalenessResult(
                service=service_name,
                is_stale=is_stale,
                age_seconds=age_seconds,
                last_heartbeat=last_heartbeat_str,
            )
        )

    return services


def create_app():
    return app


def main():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE service_health (
                    id INTEGER PRIMARY KEY,
                    service_name TEXT NOT NULL,
                    service_type TEXT NOT NULL,
                    last_heartbeat TEXT NOT NULL,
                    status TEXT
                )
                """
            )
        )
        now = datetime.utcnow()
        stale_time = now - timedelta(seconds=600)
        recent_time = now - timedelta(seconds=60)
        conn.execute(
            text(
                "INSERT INTO service_health (service_name, service_type, last_heartbeat, status) "
                "VALUES (:s, :t, :h, :st)"
            ),
            {"s": "stale_svc", "t": "write_service", "h": stale_time.isoformat(), "st": "running"},
        )
        conn.execute(
            text(
                "INSERT INTO service_health (service_name, service_type, last_heartbeat, status) "
                "VALUES (:s, :t, :h, :st)"
            ),
            {"s": "healthy_svc_1", "t": "write_service", "h": recent_time.isoformat(), "st": "running"},
        )
        conn.execute(
            text(
                "INSERT INTO service_health (service_name, service_type, last_heartbeat, status) "
                "VALUES (:s, :t, :h, :st)"
            ),
            {"s": "healthy_svc_2", "t": "write_service", "h": recent_time.isoformat(), "st": "running"},
        )

    def override_get_session():
        try:
            yield TestingSessionLocal()
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/internal/probe/write_service_staleness")
    assert response.status_code == 200
    data = response.json()
    stale_count = sum(1 for d in data if d.get("is_stale"))
    assert stale_count == 1, f"Expected 1 stale, got {stale_count}"
    print("PASS")


if __name__ == "__main__":
    main()