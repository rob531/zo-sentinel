from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_dashboard_summary, DashboardSummaryResponse

router = APIRouter(prefix="/api", tags=["dashboard_summary"])


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    name="dashboard_summary",
)
def dashboard_summary(session: Session = Depends(get_session)):
    """Thin wrapper that delegates to the business logic."""
    return get_dashboard_summary(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import McpServerRegistry, McpLlmAxisScore

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and seed it with test data
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    db = SessionLocal()

    # Seed 5 servers with distinct risk tiers
    servers = [
        McpServerRegistry(server_id="srv1", risk_tier="low"),
        McpServerRegistry(server_id="srv2", risk_tier="medium"),
        McpServerRegistry(server_id="srv3", risk_tier="high"),
        McpServerRegistry(server_id="srv4", risk_tier="critical"),
        McpServerRegistry(server_id="srv5", risk_tier="low"),
    ]
    db.add_all(servers)

    # Seed corresponding LLM axis scores (one score per server)
    scores = [
        McpLlmAxisScore(server_id="srv1", axis="reliability", score=0.2),
        McpLlmAxisScore(server_id="srv2", axis="reliability", score=0.5),
        McpLlmAxisScore(server_id="srv3", axis="reliability", score=0.7),
        McpLlmAxisScore(server_id="srv4", axis="reliability", score=0.9),
        McpLlmAxisScore(server_id="srv5", axis="reliability", score=0.3),
    ]
    db.add_all(scores)
    db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app and inject the test DB session
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:
        return db

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/dashboard/summary")
    if resp.status_code != 200:
        print(f"❌ Unexpected status code: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # Expect a structure like {"summary": {"low": {"count": 2, "avg_score": ...}, ...}}
    try:
        low_tier = data["summary"]["low"]
        assert low_tier["count"] == 2, "low tier count mismatch"
    except (KeyError, AssertionError) as e:
        print(f"❌ Validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("PASS")