from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_org_risk_tiers, OrgRiskResponse

router = APIRouter(prefix="/api")


@router.get(
    "/organization/{org_id}/risk_tiers",
    response_model=OrgRiskResponse,
    name="get_org_risk_tiers",
)
def get_org_risk_tiers_endpoint(
    org_id: str,
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    return get_org_risk_tiers(org_id=org_id, days=days, session=session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Insert test data
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    test_rows = [
        ("srv1", "org123", "HIGH_RISK_ISOLATED", now - timedelta(days=5)),
        ("srv2", "org123", "CAUTION_LIMITED", now - timedelta(days=3)),
        ("srv3", "org123", "HIGH_RISK_ISOLATED", now - timedelta(days=1)),
    ]

    with SessionLocal() as sess:
        for server_id, org_id, risk_tier, last_assessed in test_rows:
            sess.execute(
                text(
                    """
                    INSERT INTO McpServerRegistry
                    (server_id, org_id, risk_tier, last_assessed)
                    VALUES (:server_id, :org_id, :risk_tier, :last_assessed)
                    """
                ),
                {
                    "server_id": server_id,
                    "org_id": org_id,
                    "risk_tier": risk_tier,
                    "last_assessed": last_assessed,
                },
            )
        sess.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app, inject test session, and run the test request
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/organization/org123/risk_tiers")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    expected_counts = {"HIGH_RISK_ISOLATED": 2, "CAUTION_LIMITED": 1}
    actual_counts = {item["risk_tier"]: item["count"] for item in payload.get("tiers", [])}
    assert actual_counts == expected_counts, f"Got {actual_counts}, expected {expected_counts}"

    print("PASS")