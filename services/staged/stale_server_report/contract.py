"""
services/staged/stale_server_report/contract.py
"""

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["stale_server_report"])


class ServerInfo(BaseModel):
    server_id: str
    name: str
    last_assessed: datetime
    risk_tier: str | None
    scan_count: int | None

    class Config:
        orm_mode = True


class StaleReport(BaseModel):
    total: int
    servers: List[ServerInfo]


@router.get(
    "/servers/stale",
    response_model=StaleReport,
    summary="Return servers whose last assessment is older than N days",
)
def get_stale_servers(
    days: int = Query(..., ge=0, description="Number of days to consider a server stale"),
    db: Session = Depends(get_session),
):
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(McpServerRegistry)
        .where(McpServerRegistry.last_assessed <= cutoff)
        .order_by(McpServerRegistry.last_assessed)
    )
    results = db.execute(stmt).scalars().all()
    servers = [
        ServerInfo(
            server_id=row.server_id,
            name=row.name,
            last_assessed=row.last_assessed,
            risk_tier=row.risk_tier,
            scan_count=row.scan_count,
        )
        for row in results
    ]
    return StaleReport(total=len(servers), servers=servers)


# --------------------------------------------------------------------------- #
# Self‑test (runnable with `python -m services.staged.stale_server_report.contract`)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test)
    # --------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    test_engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables using the real Base metadata
    Base.metadata.create_all(bind=test_engine)

    # Populate with three servers having varied `last_assessed` timestamps
    now = datetime.utcnow()
    sample_data = [
        {
            "server_id": "srv-001",
            "name": "Alpha",
            "last_assessed": now - timedelta(days=30),
            "risk_tier": "high",
            "scan_count": 5,
        },
        {
            "server_id": "srv-002",
            "name": "Beta",
            "last_assessed": now - timedelta(days=5),
            "risk_tier": "medium",
            "scan_count": 3,
        },
        {
            "server_id": "srv-003",
            "name": "Gamma",
            "last_assessed": now - timedelta(days=20),
            "risk_tier": "low",
            "scan_count": 7,
        },
    ]

    def seed_test_db() -> None:
        with TestSessionLocal() as db:
            for rec in sample_data:
                obj = McpServerRegistry(**rec)
                db.add(obj)
            db.commit()

    # Dependency override that yields sessions from the in‑memory engine
    async def get_test_session() -> Session:  # type: ignore
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build FastAPI app for the test
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Initialise DB and run the test client
    seed_test_db()
    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Acceptance test: request stale servers older than 10 days
    # --------------------------------------------------------------------- #
    response = client.get("/api/servers/stale?days=10")
    try:
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()
        assert "total" in payload and "servers" in payload, "Missing keys in response"
        assert payload["total"] == 2, f"Expected 2 stale servers, got {payload['total']}"
        assert len(payload["servers"]) == 2, "Server list length mismatch"
        # Ensure ordering is oldest first
        dates = [datetime.fromisoformat(s["last_assessed"]) for s in payload["servers"]]
        assert dates == sorted(dates), "Servers not ordered by oldest first"
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)