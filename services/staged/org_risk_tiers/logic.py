# services/staged/org_risk_tiers/logic.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry  # type: ignore

router = APIRouter(prefix="/api")


class TierCount(BaseModel):
    risk_tier: str
    count: int


class OrgRiskResponse(BaseModel):
    org_id: str
    days: int
    tiers: List[TierCount]


def _aggregate_risk_tiers(session: Session, org_id: str, days: int) -> List[TierCount]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(
            McpServerRegistry.risk_tier,
            func.count().label("cnt"),
        )
        .where(
            McpServerRegistry.org_id == org_id,
            McpServerRegistry.last_assessed >= cutoff,
        )
        .group_by(McpServerRegistry.risk_tier)
    )
    results = session.execute(stmt).all()
    return [TierCount(risk_tier=row[0], count=row[1]) for row in results]


@router.get(
    "/organization/{org_id}/risk_tiers",
    response_model=OrgRiskResponse,
    name="get_org_risk_tiers",
)
def get_org_risk_tiers(
    org_id: str,
    days: int = 30,
    session: Session = Depends(get_session),
) -> OrgRiskResponse:
    tiers = _aggregate_risk_tiers(session, org_id, days)
    return OrgRiskResponse(org_id=org_id, days=days, tiers=tiers)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the app models to it
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # Populate test data
    with TestSessionLocal() as db:
        now = datetime.utcnow()
        servers = [
            McpServerRegistry(
                server_id="srv1",
                org_id="org123",
                risk_tier="HIGH_RISK_ISOLATED",
                last_assessed=now - timedelta(days=5),
            ),
            McpServerRegistry(
                server_id="srv2",
                org_id="org123",
                risk_tier="CAUTION_LIMITED",
                last_assessed=now - timedelta(days=3),
            ),
            McpServerRegistry(
                server_id="srv3",
                org_id="org123",
                risk_tier="HIGH_RISK_ISOLATED",
                last_assessed=now - timedelta(days=1),
            ),
        ]
        db.add_all(servers)
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/organization/org123/risk_tiers?days=30")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    expected = {"HIGH_RISK_ISOLATED": 2, "CAUTION_LIMITED": 1}
    got = {t["risk_tier"]: t["count"] for t in data["tiers"]}
    assert got == expected, f"Expected {expected}, got {got}"
    print("PASS")