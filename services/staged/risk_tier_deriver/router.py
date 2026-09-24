"""services/staged/risk_tier_deriver/router.py

Thin FastAPI router exposing the risk‑tier derivation endpoint.
"""

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real application data layer
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Base

# Business logic (must exist in the same package)
from .logic import derive_risk_tiers  # noqa: F401

router = APIRouter(prefix="/api/scoring", tags=["risk_tier_deriver"])


class DeriveRequest(BaseModel):
    """Optional payload to limit derivation to a single server."""
    server_id: str | None = None


@router.post(
    "/derive",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Derive and persist risk tiers for servers",
)
def post_derive(
    payload: DeriveRequest = Body(default_factory=DeriveRequest),
    session: Session = Depends(get_session),
):
    """
    Derive risk tiers for all servers (or a single server) and persist the
    result in ``mcp_server_registry.risk_tier``.
    """
    try:
        result = derive_risk_tiers(session, payload.server_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return result


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test only)
    # --------------------------------------------------------------------- #
    TEST_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        TEST_DATABASE_URL, connect_args={"check_same_thread": False}
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create all tables defined in the real models
    Base.metadata.create_all(bind=engine)

    # Dependency override for the test client
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Seed test data
    # --------------------------------------------------------------------- #
    test_session = TestingSessionLocal()
    try:
        # three servers
        servers = [
            McpServerRegistry(server_id="srv1", name="Server 1", risk_tier=None),
            McpServerRegistry(server_id="srv2", name="Server 2", risk_tier=None),
            McpServerRegistry(server_id="srv3", name="Server 3", risk_tier=None),
        ]
        test_session.add_all(servers)
        test_session.flush()  # obtain PKs if needed

        # helper to create 7 axis scores with the same p_top
        def add_axis_scores(srv_id: str, p_top: float):
            scores = [
                McpLlmAxisScore(
                    server_id=srv_id,
                    label_index=i,
                    label=f"axis_{i}",
                    axis_name=f"axis_{i}",
                    p_top=p_top,
                    p_critical=0.0,
                    p_danger=0.0,
                    probs="{}",
                    decision_rule_version="v1",
                    model_version="m1",
                    scored_at="1970-01-01T00:00:00Z",
                    adapter_sha256="dummy",
                )
                for i in range(7)
            ]
            test_session.add_all(scores)

        add_axis_scores("srv1", 80.0)  # should map to TRUSTED_GENERAL
        add_axis_scores("srv2", 55.0)  # should map to TRUSTED_RESEARCH
        add_axis_scores("srv3", 25.0)  # should map to HIGH_RISK_ISOLATED

        test_session.commit()
    finally:
        test_session.close()

    # --------------------------------------------------------------------- #
    # Build FastAPI app with router and override the DB dependency
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute the endpoint
    # --------------------------------------------------------------------- #
    response = client.post("/api/scoring/derive")
    if response.status_code != 200:
        print(f"❌ Unexpected status code: {response.status_code}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    expected_keys = {"derived", "updated", "skipped"}
    if not expected_keys.issubset(data):
        print(f"❌ Missing keys in response: {expected_keys - data.keys()}", file=sys.stderr)
        sys.exit(1)

    if not (data["derived"] == 3 and data["updated"] == 3 and data["skipped"] == 0):
        print(f"❌ Unexpected result counts: {data}", file=sys.stderr)
        sys.exit(1)

    # Verify that the registry rows were updated correctly
    verification_session = TestingSessionLocal()
    try:
        tiers = {
            row.server_id: row.risk_tier
            for row in verification_session.query(McpServerRegistry).all()
        }
        if (
            tiers.get("srv1") != "TRUSTED_GENERAL"
            or tiers.get("srv2") != "TRUSTED_RESEARCH"
            or tiers.get("srv3") != "HIGH_RISK_ISOLATED"
        ):
            print(f"❌ Risk tier values not as expected: {tiers}", file=sys.stderr)
            sys.exit(1)
    finally:
        verification_session.close()

    print("PASS")