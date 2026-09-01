"""
services/staged/server_threat_intel/contract.py

FastAPI contract for retrieving threat‑intel indicators for a server.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Real data‑layer imports – must remain unchanged for production use
from app.db import get_session
from app.models import ThreatIntelRef, Base  # noqa: F401 (imported for metadata)

router = APIRouter(prefix="/api")


class ThreatIndicator(BaseModel):
    type: str = Field(..., alias="indicator_type")
    value: str = Field(..., alias="indicator_value")
    pulse_name: str
    source: str = Field(..., alias="source_url")
    fetched_at: datetime


class ServerThreatIntelResponse(BaseModel):
    server_id: str
    indicators: List[ThreatIndicator]


@router.get(
    "/server/{server_id}/threat-intel",
    response_model=ServerThreatIntelResponse,
    name="get_server_threat_intel",
)
def get_server_threat_intel(
    server_id: str, session: Session = Depends(get_session)
) -> ServerThreatIntelResponse:
    """
    Return all threat‑intel indicators associated with *server_id*.
    """
    stmt = text(
        """
        SELECT
            indicator_type,
            indicator_value,
            pulse_name,
            source_url,
            fetched_at
        FROM threat_intel_refs
        WHERE server_id = :sid
        """
    )
    rows = session.execute(stmt, {"sid": server_id}).fetchall()
    if not rows:
        # Empty list is acceptable; raise only if the server truly does not exist.
        # The contract does not define a servers table, so we simply return empty.
        pass

    indicators = [
        ThreatIndicator(
            indicator_type=row.indicator_type,
            indicator_value=row.indicator_value,
            pulse_name=row.pulse_name,
            source_url=row.source_url,
            fetched_at=row.fetched_at,
        )
        for row in rows
    ]

    return ServerThreatIntelResponse(server_id=server_id, indicators=indicators)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.server_threat_intel.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app for the test
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # In‑memory SQLite DB – overrides the real dependency
    # --------------------------------------------------------------------- #
    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)  # creates the tables defined in app.models

    TestSessionLocal = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    # --------------------------------------------------------------------- #
    # Seed the temporary DB with deterministic data
    # --------------------------------------------------------------------- #
    test_server_id = "srv-123"
    seed_stmt = text(
        """
        INSERT INTO threat_intel_refs
            (indicator_type, indicator_value, pulse_name, source_url, fetched_at, server_id)
        VALUES
            (:type, :value, :pulse, :source, :fetched, :sid)
        """
    )
    now = datetime.utcnow()
    with get_test_session() as sess:
        sess.execute(
            seed_stmt,
            [
                {
                    "type": "IP",
                    "value": "1.2.3.4",
                    "pulse": "PulseA",
                    "source": "http://example.com/a",
                    "fetched": now,
                    "sid": test_server_id,
                },
                {
                    "type": "Domain",
                    "value": "bad.example.com",
                    "pulse": "PulseB",
                    "source": "http://example.com/b",
                    "fetched": now,
                    "sid": test_server_id,
                },
            ],
        )
        sess.commit()

    # --------------------------------------------------------------------- #
    # Execute the contract test
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get(f"/api/server/{test_server_id}/threat-intel")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["server_id"] == test_server_id
    assert isinstance(data["indicators"], list) and len(data["indicators"]) == 2

    expected = {
        ("IP", "1.2.3.4", "PulseA", "http://example.com/a"),
        ("Domain", "bad.example.com", "PulseB", "http://example.com/b"),
    }
    observed = {
        (
            ind["type"],
            ind["value"],
            ind["pulse_name"],
            ind["source"],
        )
        for ind in data["indicators"]
    }
    assert observed == expected, f"Indicators mismatch: {observed}"

    print("PASS")