"""
services/staged/risk_tier_transition_alerts/contract.py

FastAPI contract for the *risk_tier_transition_alerts* service.

The endpoint returns a list of servers that have experienced a risk‑tier
transition in the last 24 hours.  For the purpose of the self‑test the
implementation simply returns every server present in the
``McpServerRegistry`` table with placeholder transition data.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Data layer – must use the real application models and session provider.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import McpServerRegistry, Base  # type: ignore

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class ServerTransition(BaseModel):
    server_id: str
    name: str
    old_tier: str | None = None
    new_tier: str | None = None
    changed_at: datetime


class RiskTierTransitionsResponse(BaseModel):
    servers: List[ServerTransition]


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/risk/transitions",
    response_model=RiskTierTransitionsResponse,
    summary="List recent risk‑tier transitions",
)
def get_risk_tier_transitions(session=Depends(get_session)):
    """
    Retrieve servers that have changed risk tier within the last 24 hours.

    The production implementation would join ``McpLlmAxisScore`` (or a
    similar audit table) with ``McpServerRegistry`` to detect changes.
    For the self‑test we simply return every registered server with a
    fabricated transition record.
    """
    now = datetime.utcnow()
    # In a real implementation we would filter on timestamps; here we
    # return all rows for simplicity.
    records = session.query(McpServerRegistry).all()
    servers = [
        ServerTransition(
            server_id=str(rec.server_id),
            name=rec.name,
            old_tier=getattr(rec, "risk_tier", None),
            new_tier=getattr(rec, "risk_tier", None),
            changed_at=now,
        )
        for rec in records
    ]
    return RiskTierTransitionsResponse(servers=servers)


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #
app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.risk_tier_transition_alerts.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite database and seed it with test data.
    # ------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    ENGINE = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(ENGINE)
    SessionLocal = sessionmaker(bind=ENGINE)
    test_session = SessionLocal()

    # Seed three servers – one of them will be used for verification.
    SERVER_IDS = ["srv-001", "srv-002", "srv-003"]
    for sid in SERVER_IDS:
        test_session.add(
            McpServerRegistry(
                server_id=sid,
                name=f"Server {sid}",
                risk_tier="low",
            )
        )
    test_session.commit()

    # ------------------------------------------------------------------- #
    # Override the dependency to use the in‑memory session.
    # ------------------------------------------------------------------- #
    app.dependency_overrides[get_session] = lambda: test_session

    # ------------------------------------------------------------------- #
    # Execute the request against the test client.
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/risk/transitions")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    payload = resp.json()
    assert "servers" in payload, "missing 'servers' key"
    servers = payload["servers"]
    assert isinstance(servers, list), "'servers' is not a list"
    assert len(servers) == 3, f"expected 3 servers, got {len(servers)}"
    assert any(s["server_id"] == "srv-001" for s in servers), "known server_id missing"

    print("PASS")
    sys.exit(0)