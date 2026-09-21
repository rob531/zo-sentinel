from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real application data layer imports
from app.db import get_session
from app.models import McpServerRegistry, Base

router = APIRouter()


class ServerRiskTierResponse(BaseModel):
    server_id: int
    risk_tier: Optional[str] = None
    last_assessed: Optional[datetime] = None
    verdict_reasoning: Optional[str] = None


@router.get(
    "/servers/{server_id}/risk-tier",
    response_model=ServerRiskTierResponse,
    name="get_server_risk_tier",
)
def get_server_risk_tier(
    server_id: int, db: Session = Depends(get_session)
) -> ServerRiskTierResponse:
    """Return the risk‑tier information for a given server."""
    record = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Server not found")

    return ServerRiskTierResponse(
        server_id=record.server_id,
        risk_tier=record.risk_tier,
        last_assessed=record.last_assessed,
        verdict_reasoning=record.verdict_reasoning,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB that uses the real models
    # ------------------------------------------------------------------- #
    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    # Create tables
    Base.metadata.create_all(TEST_ENGINE)

    # Seed a single server record
    test_server = McpServerRegistry(
        server_id=1,
        risk_tier="high",
        last_assessed=datetime.utcnow(),
        verdict_reasoning="Test reason",
    )
    with TestSessionLocal() as db:
        db.add(test_server)
        db.commit()

    # ------------------------------------------------------------------- #
    # Override the dependency that provides a DB session
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:  # type: ignore
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Build a FastAPI app that includes this router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the response
    # ------------------------------------------------------------------- #
    resp = client.get("/servers/1/risk-tier")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == 1
    assert data["risk_tier"] == "high"
    assert data["verdict_reasoning"] == "Test reason"
    assert data["last_assessed"] is not None

    print("PASS")