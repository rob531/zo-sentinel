from datetime import date, timedelta
from collections import defaultdict
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter()


class TierExposure(BaseModel):
    tier: str = Field(..., description="Risk tier name")
    server_count: int = Field(..., description="Number of servers in this tier")
    total_exposure_days: int = Field(..., description="Sum of days each server has been in its current tier")
    avg_exposure_days: float = Field(..., description="Average exposure days per server in this tier")


class RiskExposureResponse(BaseModel):
    period_days: int = Field(..., description="Number of days looked back")
    tiers: List[TierExposure] = Field(..., description="Aggregated exposure per tier")
    overall_total_exposure_days: int = Field(..., description="Sum of exposure days across all tiers")


@router.get(
    "/api/dashboard/risk-exposure",
    response_model=RiskExposureResponse,
    summary="Risk exposure dashboard",
)
def get_risk_exposure(
    days: int = Query(30, ge=1, description="Look‑back period in days"),
    session: Session = Depends(get_session),
):
    """
    Compute how many days each server has been in its current risk tier,
    aggregated by tier, for the given look‑back period.
    """
    cutoff_date = date.today() - timedelta(days=days)

    # Pull relevant rows
    rows = (
        session.query(McpServerRegistry.risk_tier, McpServerRegistry.last_assessed)
        .filter(McpServerRegistry.last_assessed >= cutoff_date)
        .all()
    )

    if not rows:
        return RiskExposureResponse(
            period_days=days,
            tiers=[],
            overall_total_exposure_days=0,
        )

    # Aggregate in‑memory (portable across DB back‑ends)
    agg = defaultdict(lambda: {"server_count": 0, "total_exposure_days": 0})

    for tier, last_assessed in rows:
        if not last_assessed:
            continue
        days_in_tier = (date.today() - last_assessed.date()).days
        days_in_tier = max(0, days_in_tier)
        agg[tier]["server_count"] += 1
        agg[tier]["total_exposure_days"] += days_in_tier

    tiers: List[TierExposure] = []
    overall_total = 0

    for tier, data in agg.items():
        server_count = data["server_count"]
        total_days = data["total_exposure_days"]
        avg_days = round(total_days / server_count, 2) if server_count else 0.0
        tiers.append(
            TierExposure(
                tier=tier,
                server_count=server_count,
                total_exposure_days=total_days,
                avg_exposure_days=avg_days,
            )
        )
        overall_total += total_days

    return RiskExposureResponse(
        period_days=days,
        tiers=tiers,
        overall_total_exposure_days=overall_total,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors real models)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(bind=engine)

    # Seed data
    today = date.today()
    seed = [
        # HIGH_RISK_ISOLATED: 7 and 14 days ago
        {
            "server_id": "srv-hr-1",
            "risk_tier": "HIGH_RISK_ISOLATED",
            "last_assessed": today - timedelta(days=7),
        },
        {
            "server_id": "srv-hr-2",
            "risk_tier": "HIGH_RISK_ISOLATED",
            "last_assessed": today - timedelta(days=14),
        },
        # CAUTION_LIMITED: 3 and 5 days ago
        {
            "server_id": "srv-cl-1",
            "risk_tier": "CAUTION_LIMITED",
            "last_assessed": today - timedelta(days=3),
        },
        {
            "server_id": "srv-cl-2",
            "risk_tier": "CAUTION_LIMITED",
            "last_assessed": today - timedelta(days=5),
        },
        # TRUSTED_GENERAL: 1 day ago
        {
            "server_id": "srv-tg-1",
            "risk_tier": "TRUSTED_GENERAL",
            "last_assessed": today - timedelta(days=1),
        },
    ]

    with SessionLocal() as db:
        for rec in seed:
            db.add(
                McpServerRegistry(
                    server_id=rec["server_id"],
                    risk_tier=rec["risk_tier"],
                    last_assessed=rec["last_assessed"],
                )
            )
        db.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request & validate contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/dashboard/risk-exposure?days=30")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    tiers = {t["tier"]: t for t in data.get("tiers", [])}

    # Basic contract checks
    if len(tiers) < 2:
        print("FAIL: Expected at least two tiers in response", file=sys.stderr)
        sys.exit(1)

    high = tiers.get("HIGH_RISK_ISOLATED")
    if not high or high["server_count"] != 2:
        print("FAIL: HIGH_RISK_ISOLATED server_count mismatch", file=sys.stderr)
        sys.exit(1)

    if high["total_exposure_days"] < 20:
        print("FAIL: HIGH_RISK_ISOLATED total_exposure_days too low", file=sys.stderr)
        sys.exit(1)

    print("PASS")