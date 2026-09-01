from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_distribution, RiskDistributionResponse

router = APIRouter(prefix="/api", tags=["risk_distribution_summary"])


@router.get(
    "/risk/distribution",
    response_model=RiskDistributionResponse,
    summary="Get distribution of risk tiers across all servers",
)
def risk_distribution(session: Session = Depends(get_session)):
    """Return the count of servers per risk tier and the total number of servers."""
    return get_risk_distribution(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the original dependency to be overridden
    from app.db import get_session as original_get_session
    # Import the models that the logic layer expects
    from app.models import McpServerRegistry, McpLlmAxisScore, Base

    # ----------------------------------------------------------------------- #
    # Set up an in‑memory SQLite database and create the required tables
    # ----------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    # ----------------------------------------------------------------------- #
    # Seed the database with a known risk tier distribution
    # ----------------------------------------------------------------------- #
    db = SessionLocal()
    db.add_all(
        [
            McpServerRegistry(id=1, risk_tier="low"),
            McpServerRegistry(id=2, risk_tier="medium"),
            McpServerRegistry(id=3, risk_tier="high"),
            McpServerRegistry(id=4, risk_tier="low"),
        ]
    )
    # The axis scores table is not required for this endpoint, but we create
    # at least one row to satisfy any foreign‑key constraints that may exist.
    db.add(McpLlmAxisScore(id=1, server_id=1, axis_name="dummy", score=0.0))
    db.commit()
    db.close()

    # ----------------------------------------------------------------------- #
    # Dependency override: provide a fresh session from the in‑memory DB
    # ----------------------------------------------------------------------- #
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ----------------------------------------------------------------------- #
    # Assemble the FastAPI app and run the test client
    # ----------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[original_get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/risk/distribution")
    if response.status_code != 200:
        print(f"FAIL – unexpected status code: {response.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = response.json()
    expected_distribution = {"low": 2, "medium": 1, "high": 1}
    if payload.get("distribution") != expected_distribution or payload.get("total_servers") != 4:
        print(f"FAIL – unexpected payload: {payload}", file=sys.stderr)
        sys.exit(1)

    print("PASS")