# services/staged/canonical_family_drift/logic.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpServerRegistry

router = APIRouter()


class DriftResponse(BaseModel):
    server_id: str
    canonical_family: Optional[str] = None
    drift: Dict[str, Any] = {}


@router.get(
    "/family/drift",
    response_model=DriftResponse,
    summary="Return drift analysis for a server's canonical family",
)
def get_family_drift(
    server_id: str,
    session: Session = Depends(get_session),
) -> DriftResponse:
    """Fetch the server registry entry and extract canonical‑family drift info."""
    reg = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not reg:
        raise HTTPException(status_code=404, detail="Server not found")

    # `meta` may be a JSON string or a dict; normalise to dict
    meta_raw = reg.meta
    if isinstance(meta_raw, str):
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(meta_raw, dict):
        meta = meta_raw
    else:
        meta = {}

    canonical_family = meta.get("canonical_family")
    drift = meta.get("drift", {})

    return DriftResponse(
        server_id=server_id,
        canonical_family=canonical_family,
        drift=drift,
    )


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # --------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # --------------------------------------------------------------------- #
    # Dependency override for the test FastAPI app
    # --------------------------------------------------------------------- #
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # --------------------------------------------------------------------- #
    # Seed test data
    # --------------------------------------------------------------------- #
    test_server_id = "test-server-1"
    test_meta = {
        "canonical_family": "family‑alpha",
        "drift": {"risk_tier": {"old": "low", "new": "high"}},
    }

    with TestingSessionLocal() as db:
        db.add(
            McpServerRegistry(
                server_id=test_server_id,
                name="Test Server",
                meta=json.dumps(test_meta),
                # optional columns left as None / defaults
                confidence=None,
                description=None,
                first_seen=None,
                last_assessed=None,
                last_scanned=None,
                last_seen=None,
                registry_source=None,
                risk_tier=None,
                scan_count=None,
                trust_score=None,
                url=None,
                verdict=None,
                verdict_reasoning=None,
            )
        )
        db.commit()

    # --------------------------------------------------------------------- #
    # Run the test client
    # --------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.get(f"/family/drift?server_id={test_server_id}")
    if resp.status_code != 200:
        print(f"❌ Unexpected status: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    expected = {
        "server_id": test_server_id,
        "canonical_family": "family‑alpha",
        "drift": {"risk_tier": {"old": "low", "new": "high"}},
    }

    if data != expected:
        print(f"❌ Unexpected payload: {data!r}", file=sys.stderr)
        sys.exit(1)

    print("PASS")