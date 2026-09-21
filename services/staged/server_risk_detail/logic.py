# services/staged/server_risk_detail/logic.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import Dict

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter()


class AxisDetail(BaseModel):
    label: str
    p_top: float


class RiskDetail(BaseModel):
    axes: Dict[str, AxisDetail]
    overall: float
    risk_tier: str
    criteria_version: str


def _determine_risk_tier(rows):
    # Base tier from the first row (all rows share the same tier in normal data)
    tier = rows[0].risk_tier if rows else "UNKNOWN"
    # Override: any axis labelled "CRITICAL" forces tier to "CRITICAL"
    for r in rows:
        if r.label and r.label.upper() == "CRITICAL":
            return "CRITICAL"
    return tier


@router.get(
    "/api/servers/{server_id}/risk",
    response_model=RiskDetail,
    tags=["server_risk_detail"],
)
def get_server_risk_detail(
    server_id: int, db: Session = Depends(get_session)
) -> RiskDetail:
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Server risk data not found")

    axes: Dict[str, AxisDetail] = {}
    for r in rows:
        axes[r.axis] = AxisDetail(label=r.label, p_top=r.p_top)

    overall = rows[0].overall_risk
    criteria_version = rows[0].criteria_version
    risk_tier = _determine_risk_tier(rows)

    return RiskDetail(
        axes=axes,
        overall=overall,
        risk_tier=risk_tier,
        criteria_version=criteria_version,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and seed it with minimal test data
    # ------------------------------------------------------------------- #
    TEST_ENGINE = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(TEST_ENGINE)
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data for server_id = 1
    test_db = TestSessionLocal()
    sample_rows = [
        McpLlmAxisScore(
            server_id=1,
            axis="confidentiality",
            label="HIGH",
            p_top=0.85,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
        McpLlmAxisScore(
            server_id=1,
            axis="integrity",
            label="CRITICAL",
            p_top=0.95,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
        McpLlmAxisScore(
            server_id=1,
            axis="availability",
            label="LOW",
            p_top=0.30,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
        McpLlmAxisScore(
            server_id=1,
            axis="authenticity",
            label="MEDIUM",
            p_top=0.60,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
        McpLlmAxisScore(
            server_id=1,
            axis="nonrepudiation",
            label="LOW",
            p_top=0.25,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
        McpLlmAxisScore(
            server_id=1,
            axis="privacy",
            label="HIGH",
            p_top=0.80,
            overall_risk=0.78,
            risk_tier="MEDIUM",
            criteria_version="v1",
        ),
    ]
    test_db.add_all(sample_rows)
    test_db.commit()
    test_db.close()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with the router and override the DB dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/servers/1/risk")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Expect 6 axes plus overall, risk_tier overridden to CRITICAL
    expected_axes = {
        "confidentiality",
        "integrity",
        "availability",
        "authenticity",
        "nonrepudiation",
        "privacy",
    }
    assert set(data["axes"].keys()) == expected_axes
    assert data["overall"] == 0.78
    assert data["risk_tier"] == "CRITICAL"
    assert data["criteria_version"] == "v1"

    print("PASS")