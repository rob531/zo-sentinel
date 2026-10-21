from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_attestation_status, get_fleet_summary

router = APIRouter(prefix="/api")


class AttestationDetails(BaseModel):
    status: str
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    stale: bool


class AttestationStatusResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    attestation: Optional[AttestationDetails] = None


class FleetSummaryResponse(BaseModel):
    total_servers: int
    with_attestation: int
    without_attestation: int
    stale_count: int


@router.get("/attestation/status", response_model=AttestationStatusResponse)
def attestation_status(
    server_id: str,
    session=Depends(get_session),
) -> AttestationStatusResponse:
    return get_attestation_status(server_id, session)


@router.get("/attestation/fleet-summary", response_model=FleetSummaryResponse)
def attestation_fleet_summary(
    session=Depends(get_session),
) -> FleetSummaryResponse:
    return get_fleet_summary(session)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    attestation_type TEXT,
                    status TEXT,
                    issued_at TEXT,
                    expires_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, verdict)
                VALUES
                    ('server-fresh', 'Fresh Server', 'low', 'approved'),
                    ('server-unattested', 'Unattested Server', 'medium', 'review'),
                    ('server-stale', 'Stale Server', 'high', 'rejected')
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations
                    (server_id, attestation_type, status, issued_at, expires_at)
                VALUES
                    (
                        'server-fresh',
                        'mcp',
                        'valid',
                        :issued_at,
                        :fresh_expires_at
                    ),
                    (
                        'server-stale',
                        'mcp',
                        'valid',
                        :stale_issued_at,
                        :stale_expires_at
                    )
                """
            ),
            {
                "issued_at": (now - timedelta(hours=1)).isoformat(),
                "fresh_expires_at": (now + timedelta(days=7)).isoformat(),
                "stale_issued_at": (now - timedelta(days=14)).isoformat(),
                "stale_expires_at": (now - timedelta(days=1)).isoformat(),
            },
        )

    test_session = TestSession()
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    try:
        with TestClient(test_app) as client:
            status_response = client.get(
                "/api/attestation/status",
                params={"server_id": "server-fresh"},
            )
            assert status_response.status_code == 200
            status_payload = status_response.json()
            assert status_payload["server_id"] == "server-fresh"
            assert status_payload["name"] == "Fresh Server"
            assert status_payload["attestation"] is not None
            assert status_payload["attestation"]["stale"] is False

            fleet_response = client.get("/api/attestation/fleet-summary")
            assert fleet_response.status_code == 200
            fleet_payload = fleet_response.json()
            assert fleet_payload["total_servers"] == 3
            assert fleet_payload["with_attestation"] == 2
            assert fleet_payload["without_attestation"] == 1
            assert fleet_payload["stale_count"] == 1
    finally:
        test_session.close()
        test_engine.dispose()

    print("PASS")