# services/staged/write_service_health/contract.py
import json
from datetime import datetime
from typing import List

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

# Real application dependency – will be overridden in the self‑test.
from app.db import get_session

router = APIRouter()


class HealthBeat(BaseModel):
    service: str = Field(..., description="Name of the service")
    status: str = Field(..., description="Current status")
    meta: dict = Field(default_factory=dict, description="Arbitrary metadata")


class HealthStatus(BaseModel):
    service: str
    status: str
    last_heartbeat: datetime
    meta: dict


@router.post("/health/beat", response_model=HealthBeat)
def beat(payload: HealthBeat, session: Session = Depends(get_session)):
    """
    Upsert a row in the ``service_health`` table.
    """
    stmt = text(
        """
        INSERT INTO service_health (service, status, last_heartbeat, meta)
        VALUES (:service, :status, :last_heartbeat, :meta)
        ON CONFLICT(service) DO UPDATE SET
            status = excluded.status,
            last_heartbeat = excluded.last_heartbeat,
            meta = excluded.meta
        """
    )
    try:
        session.execute(
            stmt,
            {
                "service": payload.service,
                "status": payload.status,
                "last_heartbeat": datetime.utcnow(),
                "meta": json.dumps(payload.meta),
            },
        )
        session.commit()
    except Exception as exc:  # pragma: no cover
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    return payload


@router.get("/health/status", response_model=List[HealthStatus])
def status(session: Session = Depends(get_session)):
    """
    Return the full ``service_health`` table ordered by most recent heartbeat.
    """
    stmt = text(
        """
        SELECT service, status, last_heartbeat, meta
        FROM service_health
        ORDER BY last_heartbeat DESC
        """
    )
    rows = session.execute(stmt).fetchall()
    result: List[HealthStatus] = []
    for row in rows:
        result.append(
            HealthStatus(
                service=row.service,
                status=row.status,
                last_heartbeat=row.last_heartbeat,
                meta=json.loads(row.meta) if row.meta else {},
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Self‑test (runnable with ``python -m services.staged.write_service_health.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors the real ``service_health`` schema)
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS service_health (
                    service TEXT PRIMARY KEY,
                    status TEXT,
                    last_heartbeat TIMESTAMP,
                    meta TEXT
                )
                """
            )
        )

    # --------------------------------------------------------------------- #
    # FastAPI app wiring
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Seed two services via the POST endpoint
    # --------------------------------------------------------------------- #
    seed_payloads = [
        {"service": "svc_one", "status": "healthy", "meta": {"env": "dev"}},
        {"service": "svc_two", "status": "degraded", "meta": {"env": "prod"}},
    ]

    for payload in seed_payloads:
        resp = client.post("/health/beat", json=payload)
        assert resp.status_code == 200, f"POST failed: {resp.text}"

    # --------------------------------------------------------------------- #
    # Verify GET returns both seeded rows
    # --------------------------------------------------------------------- #
    resp = client.get("/health/status")
    assert resp.status_code == 200, f"GET failed: {resp.text}"
    data = resp.json()
    returned_services = {item["service"] for item in data}
    assert {"svc_one", "svc_two"} <= returned_services, "Missing seeded services"

    print("PASS")
    sys.exit(0)