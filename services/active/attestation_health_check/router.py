# services/active/attestation_health_check/router.py
# deps: requests
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_attestation_health

router = APIRouter(prefix="/api/attestation", tags=["attestation_health_check"])


class AttestationHealthResponse(BaseModel):
    count: int = Field(..., description="Number of attestations in the last 24h")
    oldest: Optional[datetime] = Field(
        None, description="Timestamp of the oldest attestation in the window"
    )
    newest: Optional[datetime] = Field(
        None, description="Timestamp of the newest attestation in the window"
    )
    average_response_time: Optional[float] = Field(
        None, description="Average response_time of attestations in the window"
    )


@router.get("/health", response_model=AttestationHealthResponse)
def health(session: Session = Depends(get_session)):
    """
    Return health metrics for attestations written in the last 24 hours.
    A 200 response is emitted only when ≥4 attestations have been written
    in the period (i.e. at least one every 6 hours).
    """
    result = compute_attestation_health(session)
    if result.count < 4:
        raise HTTPException(
            status_code=503,
            detail="Insufficient attestation frequency (need ≥4 in 24h)"
        )
    return result


# --------------------------------------------------------------------------- #
# Self‑test ---------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite with schema matching the health response shape.
    # The self‑test overrides get_session to return a throwaway SQLite session;
    # the module's own data layer (compute_attestation_health) still reads from
    # write_service in production.
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Minimal table so the session is usable
    from app.db import Base
    from app.models import McpServerRegistry

    Base.metadata.create_all(engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session

    # Minimal health response for the test: override compute_attestation_health
    # to return a deterministic fixture without touching write_service.
    import services.active.attestation_health_check.router as mod
    now = datetime.utcnow()
    mod.compute_attestation_health = lambda _: AttestationHealthResponse(
        count=5,
        oldest=now - timedelta(hours=20),
        newest=now,
        average_response_time=0.45,
    )

    from .logic import compute_attestation_health as real_health

    # Restore real impl so the router sees it
    mod.compute_attestation_health = real_health

    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/attestation/health")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    expected_keys = {"count", "oldest", "newest", "average_response_time"}
    if not expected_keys.issubset(data):
        print(f"FAIL: missing keys in response: {data}", file=sys.stderr)
        sys.exit(1)

    if data["count"] != 5:
        print(f"FAIL: expected count 5, got {data['count']}", file=sys.stderr)
        sys.exit(1)

    oldest = datetime.fromisoformat(data["oldest"])
    newest = datetime.fromisoformat(data["newest"])
    if newest <= oldest:
        print("FAIL: newest must be > oldest", file=sys.stderr)
        sys.exit(1)

    # Test 503 case: fewer than 4 attestations
    mod.compute_attestation_health = lambda _: AttestationHealthResponse(
        count=2,
        oldest=now,
        newest=now,
        average_response_time=0.1,
    )
    resp2 = client.get("/api/attestation/health")
    if resp2.status_code != 503:
        print(f"FAIL: expected 503 for count<4, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
