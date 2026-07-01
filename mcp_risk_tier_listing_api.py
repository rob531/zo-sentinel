"""GET /risk-tiers -- distinct risk_tier values from mcp_server_registry.

Hollow-build-free FastAPI router: uses the REAL app data layer
(app.db.get_session / app.models.McpServerRegistry), no inline stubs.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class RiskTierListResponse(BaseModel):
    tiers: List[str]


@router.get("/risk-tiers", response_model=RiskTierListResponse)
def list_risk_tiers(db: Session = Depends(get_session)):
    """Return the distinct risk_tier values present in mcp_server_registry."""
    rows = (
        db.execute(
            select(McpServerRegistry.risk_tier)
            .where(McpServerRegistry.risk_tier.isnot(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    # sort for stable ordering; strip None (already filtered but belt-and-suspenders)
    tiers = sorted(t for t in rows if t)
    return RiskTierListResponse(tiers=tiers)


# --- Self-test ---------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    # Build an in-memory SQLite engine for the test so it runs in CI with no Postgres.
    _test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create the real table schema.
    from app.models import Base
    Base.metadata.create_all(bind=_test_engine)

    _TestSession = sessionmaker(bind=_test_engine, autoflush=False, autocommit=False)

    # Seed known tiers.
    _seed = [
        McpServerRegistry(server_id="s1", name="a", risk_tier="low"),
        McpServerRegistry(server_id="s2", name="b", risk_tier="medium"),
        McpServerRegistry(server_id="s3", name="c", risk_tier="high"),
        McpServerRegistry(server_id="s4", name="d", risk_tier="low"),
        McpServerRegistry(server_id="s5", name="e", risk_tier="critical"),
        # NULL risk_tier row must not appear in output
        McpServerRegistry(server_id="s6", name="f", risk_tier=None),
    ]
    with _TestSession() as _sess:
        _sess.add_all(_seed)
        _sess.commit()

    # Build a standalone test app that mounts our router.
    from app.db import get_session as _real_get_session

    _test_app = FastAPI()
    _test_app.include_router(router)

    def _override_session():
        s: Session = _TestSession()
        try:
            yield s
        finally:
            s.close()

    _test_app.dependency_overrides[_real_get_session] = _override_session
    _client = TestClient(_test_app)

    resp = _client.get("/risk-tiers")
    if resp.status_code != 200:
        print(f"FAIL: status {resp.status_code} -- {resp.text}")
        sys.exit(1)

    tiers = resp.json().get("tiers", [])
    expected = sorted(["low", "medium", "high", "critical"])
    if sorted(tiers) != expected:
        print(f"FAIL: got {tiers}, expected {expected}")
        sys.exit(1)

    # Also verify the endpoint is reachable via the main app (router auto-mounted).
    from app.main import app as _main_app
    _main_app.dependency_overrides[_real_get_session] = _override_session
    _main_client = TestClient(_main_app)
    resp2 = _main_client.get("/risk-tiers")
    if resp2.status_code != 200:
        print(f"FAIL: main app mount returned {resp2.status_code} -- {resp2.text}")
        sys.exit(1)

    print("PASS")
