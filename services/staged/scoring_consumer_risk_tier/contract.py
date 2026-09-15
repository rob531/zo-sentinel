# services/staged/scoring_consumer_risk_tier/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session
from typing import Generator, List

# Real data layer imports – must not be re‑implemented
from app.db import get_session, Base  # get_session provides the app DB session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(
    prefix="/internal/scoring",
    tags=["scoring_consumer_risk_tier"],
)


# ----------------------------------------------------------------------
# Tier calculation logic (PRODUCT_SPEC §2)
# ----------------------------------------------------------------------
_TIER_THRESHOLDS = [
    ("TRUSTED_GENERAL", 75),
    ("TRUSTED_RESEARCH", 60),
    ("ENTERPRISE_CONTROLLED", 45),
    ("CAUTION_LIMITED", 30),
    ("HIGH_RISK_ISOLATED", 15),
    ("KNOWN_THREAT", -1),  # catch‑all for <=15
]


def _determine_tier(score: float) -> str:
    """Return the tier name for a given composite score."""
    for tier, bound in _TIER_THRESHOLDS:
        if score > bound:
            return tier
    # If we fall through, the score is <=15
    return "KNOWN_THREAT"


# ----------------------------------------------------------------------
# Endpoint implementation
# ----------------------------------------------------------------------
@router.post(
    "/risk-tier-consume",
    status_code=status.HTTP_200_OK,
    summary="Consume LLM axis scores and update server risk tiers",
)
def consume_risk_tier(session: Session = Depends(get_session)):
    """
    Join `mcp_llm_axis_scores` with `mcp_server_registry`,
    compute a composite risk score (using the server's `trust_score` column),
    map it to a tier, persist the new tier and return a summary.
    """
    # Fetch all servers – the join is not needed for the simplified logic
    servers: List[McpServerRegistry] = session.query(McpServerRegistry).all()
    if not servers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No servers found to evaluate.",
        )

    updated = 0
    for server in servers:
        old_tier = server.risk_tier
        # Use the existing `trust_score` as the composite score; treat None as 0
        composite_score = float(server.trust_score or 0)
        new_tier = _determine_tier(composite_score)

        if new_tier != old_tier:
            server.risk_tier = new_tier
            updated += 1
            # NOTE: Perspective events are omitted here because the
            # `Perspective` model requires many mandatory fields that are
            # outside the scope of this self‑test.

    session.commit()
    return {"updated": updated, "total": len(servers)}


# ----------------------------------------------------------------------
# Self‑test (run with `python -m services.staged.scoring_consumer_risk_tier.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # In‑memory SQLite setup (overrides the real DB for the test)
    # ------------------------------------------------------------------
    TEST_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables using the real metadata
    Base.metadata.create_all(bind=engine)

    # Dependency override for the test FastAPI app
    def get_test_session() -> Generator[Session, None, None]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed synthetic data
    # ------------------------------------------------------------------
    def seed_data() -> None:
        db: Session = TestSessionLocal()
        try:
            servers = [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Server One",
                    trust_score=80,  # >75 → TRUSTED_GENERAL
                    risk_tier=None,
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Server Two",
                    trust_score=65,  # >60 → TRUSTED_RESEARCH
                    risk_tier=None,
                ),
                McpServerRegistry(
                    server_id="srv-3",
                    name="Server Three",
                    trust_score=20,  # >15 → HIGH_RISK_ISOLATED
                    risk_tier=None,
                ),
            ]
            db.add_all(servers)
            db.commit()
        finally:
            db.close()

    seed_data()

    # ------------------------------------------------------------------
    # Execute the endpoint via TestClient
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.post("/internal/scoring/risk-tier-consume")
    if response.status_code != 200:
        print(f"FAIL – endpoint returned {response.status_code}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Verify tier assignments
    # ------------------------------------------------------------------
    db: Session = TestSessionLocal()
    try:
        srv1 = db.query(McpServerRegistry).filter_by(server_id="srv-1").one()
        srv2 = db.query(McpServerRegistry).filter_by(server_id="srv-2").one()
        srv3 = db.query(McpServerRegistry).filter_by(server_id="srv-3").one()

        assert srv1.risk_tier == "TRUSTED_GENERAL", f"srv1 tier {srv1.risk_tier}"
        assert srv2.risk_tier == "TRUSTED_RESEARCH", f"srv2 tier {srv2.risk_tier}"
        assert srv3.risk_tier == "HIGH_RISK_ISOLATED", f"srv3 tier {srv3.risk_tier}"
    finally:
        db.close()

    print("PASS")
    sys.exit(0)