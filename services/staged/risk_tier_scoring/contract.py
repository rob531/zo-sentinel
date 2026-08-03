# services/staged/risk_tier_scoring/contract.py
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Base
from services.staged.risk_tier_scoring.logic import compute_tier

router = APIRouter(prefix="/api/risk/tier")


class AxesSummary(BaseModel):
    critical_count: int
    danger_count: int
    top_count: int


class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    decision_rule_version: str
    model_version: str
    scored_at: Optional[datetime]
    axes_summary: AxesSummary


@router.get("/{server_id}", response_model=RiskTierResponse)
def get_risk_tier(
    server_id: str, session: Depends = Depends(get_session)
) -> RiskTierResponse:
    # fetch scores for the server
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    # compute tier using shared logic
    tier = compute_tier(server_id)

    # summary counts
    critical_cnt = sum(1 for s in scores if s.label == "CRITICAL")
    danger_cnt = sum(1 for s in scores if s.label == "DANGER")
    top_cnt = sum(1 for s in scores if s.label == "TOP")

    # latest scored_at
    latest_scored_at = (
        max((s.scored_at for s in scores), default=None) if scores else None
    )

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=tier,
        decision_rule_version="v1.0",
        model_version="model-1.0",
        scored_at=latest_scored_at,
        axes_summary=AxesSummary(
            critical_count=critical_cnt,
            danger_count=danger_cnt,
            top_count=top_cnt,
        ),
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.risk_tier_scoring.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build a throw‑away SQLite DB and override the app dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        # server with a CRITICAL label on one axis
        db.add(
            McpLlmAxisScore(
                server_id="critical_server",
                axis_name="axis_1",
                p_critical=0.9,
                p_danger=0.05,
                p_top=0.05,
                label="CRITICAL",
                label_index=0,
                scored_at=datetime.utcnow(),
            )
        )
        # server with all TOP labels and high p_top values
        for i in range(7):
            db.add(
                McpLlmAxisScore(
                    server_id="top_server",
                    axis_name=f"axis_{i}",
                    p_critical=0.0,
                    p_danger=0.1,
                    p_top=0.9,
                    label="TOP",
                    label_index=2,
                    scored_at=datetime.utcnow(),
                )
            )
        # server with no scores (will trigger INSUFFICIENT)
        db.add(McpServerRegistry(server_id="missing_server", risk_tier="UNKNOWN"))
        db.commit()

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Run assertions
    # ------------------------------------------------------------------- #
    def assert_tier(sid: str, expected: str):
        resp = client.get(f"/api/risk/tier/{sid}")
        assert resp.status_code == 200, f"{sid} status {resp.status_code}"
        data = resp.json()
        assert data["risk_tier"] == expected, f"{sid} tier {data['risk_tier']} != {expected}"

    assert_tier("critical_server", "HIGH_RISK_ISOLATED")
    assert_tier("top_server", "TRUSTED_GENERAL")
    assert_tier("missing_server", "INSUFFICIENT")

    print("PASS")