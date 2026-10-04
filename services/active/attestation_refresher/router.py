# services/active/attestation_refresher/router.py
# deps: fastapi, requests
"""FastAPI router for refreshing server attestation data.

Refreshes servers with stale attestation records (last_assessed older than threshold).
The service queries McpServerRegistry for servers needing attestation refresh,
updates their last_assessed timestamp, and returns the refreshed server list.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/attestation", tags=["attestation_refresher"])

DEFAULT_STALE_THRESHOLD_DAYS = 30


class RefreshedServer(BaseModel):
    server_id: str = Field(..., description="Unique server identifier")
    name: str = Field(..., description="Server name")
    last_assessed: Optional[datetime] = Field(
        None, description="Previous assessment timestamp"
    )
    refresh_status: str = Field(..., description="Status of the refresh operation")


class AttestationRefreshResponse(BaseModel):
    refreshed_count: int = Field(..., description="Number of servers refreshed")
    threshold_days: int = Field(..., description="Staleness threshold in days")
    refreshed_servers: List[RefreshedServer] = Field(
        default_factory=list, description="List of refreshed servers"
    )


def get_stale_servers(
    db: Session,
    threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> List[McpServerRegistry]:
    """Query servers with attestation data older than threshold_days."""
    cutoff = datetime.utcnow() - timedelta(days=threshold_days)
    return (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.last_assessed < cutoff)
        .all()
    )


def refresh_server_attestation(
    server: McpServerRegistry,
) -> RefreshedServer:
    """Refresh a single server's attestation record."""
    previous_assessed = server.last_assessed
    server.last_assessed = datetime.utcnow()
    return RefreshedServer(
        server_id=server.server_id,
        name=server.name,
        last_assessed=previous_assessed,
        refresh_status="refreshed",
    )


@router.get(
    "/refresh",
    response_model=AttestationRefreshResponse,
    summary="Refresh stale server attestations",
)
def refresh_attestations(
    threshold_days: int = Field(
        default=DEFAULT_STALE_THRESHOLD_DAYS,
        ge=1,
        le=365,
        description="Staleness threshold in days",
    ),
    db: Session = Depends(get_session),
) -> AttestationRefreshResponse:
    """
    Find and refresh servers with attestation records older than threshold_days.

    Returns the count of refreshed servers and a list of affected servers.
    """
    stale_servers = get_stale_servers(db, threshold_days)
    refreshed = []

    for server in stale_servers:
        refreshed.append(refresh_server_attestation(server))

    if refreshed:
        db.commit()

    return AttestationRefreshResponse(
        refreshed_count=len(refreshed),
        threshold_days=threshold_days,
        refreshed_servers=refreshed,
    )


@router.get(
    "/refresh/{server_id}",
    response_model=RefreshedServer,
    summary="Refresh a specific server's attestation",
)
def refresh_single_attestation(
    server_id: str,
    db: Session = Depends(get_session),
) -> RefreshedServer:
    """Refresh attestation for a specific server by ID."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(
            status_code=404,
            detail=f"Server '{server_id}' not found",
        )

    result = refresh_server_attestation(server)
    db.commit()
    return result


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session as real_get_session

    # Build an in‑memory SQLite DB that mirrors the app's declarative base
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Assemble FastAPI app for the test
    app = FastAPI()
    app.dependency_overrides[real_get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # Seed test data
    now = datetime.utcnow()
    with TestSessionLocal() as db:
        # Stale server (needs refresh)
        stale_server = McpServerRegistry(
            server_id="srv-stale-001",
            name="Stale Server",
            registry_source="test",
            url="https://stale.example.com",
            last_assessed=now - timedelta(days=45),
        )
        # Fresh server (recently assessed)
        fresh_server = McpServerRegistry(
            server_id="srv-fresh-002",
            name="Fresh Server",
            registry_source="test",
            url="https://fresh.example.com",
            last_assessed=now - timedelta(days=5),
        )
        db.add_all([stale_server, fresh_server])
        db.commit()

    # Test 1: Refresh stale servers
    resp = client.get("/api/attestation/refresh", params={"threshold_days": 30})
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data["refreshed_count"] != 1:
        print(f"FAIL: expected 1 refreshed server, got {data['refreshed_count']}", file=sys.stderr)
        sys.exit(1)

    if data["threshold_days"] != 30:
        print(f"FAIL: expected threshold_days=30, got {data['threshold_days']}", file=sys.stderr)
        sys.exit(1)

    if len(data["refreshed_servers"]) != 1:
        print(f"FAIL: expected 1 server in list, got {len(data['refreshed_servers'])}", file=sys.stderr)
        sys.exit(1)

    if data["refreshed_servers"][0]["server_id"] != "srv-stale-001":
        print(f"FAIL: expected srv-stale-001, got {data['refreshed_servers'][0]['server_id']}", file=sys.stderr)
        sys.exit(1)

    if data["refreshed_servers"][0]["refresh_status"] != "refreshed":
        print(f"FAIL: expected status 'refreshed', got {data['refreshed_servers'][0]['refresh_status']}", file=sys.stderr)
        sys.exit(1)

    # Test 2: Refresh specific server
    resp2 = client.get("/api/attestation/refresh/srv-fresh-002")
    if resp2.status_code != 200:
        print(f"FAIL: expected 200 for single refresh, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    single_data = resp2.json()
    if single_data["server_id"] != "srv-fresh-002":
        print(f"FAIL: expected srv-fresh-002, got {single_data['server_id']}", file=sys.stderr)
        sys.exit(1)

    # Test 3: Refresh non-existent server
    resp3 = client.get("/api/attestation/refresh/nonexistent-id")
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for nonexistent server, got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
