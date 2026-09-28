# services/staged/overall_risk_tier/contract.py
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class AxisInfo(BaseModel):
    label: str
    p_top: float


class OverallRiskResponse(BaseModel):
    axes: Dict[str, AxisInfo]
    overall: float
    risk_tier: str
    criteria_version: str


@router.get(
    "/risk/overall/{server_id}",
    response_model=OverallRiskResponse,
    name="overall_risk_tier:get_overall_risk",
)
def get_overall_risk(
    server_id: int,
    db: Session = Depends(get_session),
) -> OverallRiskResponse:
    """
    Return the per‑axis scores together with an overall risk tier.
    A CRITICAL axis (p_critical > 0.5) forces the tier to be "CRITICAL".
    """
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    axes: Dict[str, AxisInfo] = {}
    overall_sum = 0.0
    for s in scores:
        axes[s.axis_name] = AxisInfo(label=s.label, p_top=s.p_top)
        overall_sum += s.p_top

    overall = overall_sum / len(scores) if scores else 0.0
    # Tier logic: any critical probability > 0.5 forces CRITICAL tier
    tier = (
        "CRITICAL"
        if any(s.p_critical is not None and s.p_critical > 0.5 for s in scores)
        else "NORMAL"
    )
    criteria_version = scores[0].decision_rule_version if scores else "unknown"

    return OverallRiskResponse(
        axes=axes,
        overall=overall,
        risk_tier=tier,
        criteria_version=criteria_version,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.overall_risk_tier.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real schema
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed minimal data
    db = SessionLocal()
    try:
        # Two axes – one critical to trigger the tier override
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=1,
                    axis_name="confidentiality",
                    label="Confidentiality",
                    label_index=0,
                    adapter_sha256="a" * 64,
                    model_version="v1",
                    decision_rule_version="v1",
                    p_top=0.7,
                    p_critical=0.6,
                    p_danger=0.2,
                    probs="{}",
                    scored_at=datetime.utcnow(),
                    escalated=False,
                    escalated_to=None,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=1,
                    axis_name="integrity",
                    label="Integrity",
                    label_index=1,
                    adapter_sha256="b" * 64,
                    model_version="v1",
                    decision_rule_version="v1",
                    p_top=0.4,
                    p_critical=0.3,
                    p_danger=0.1,
                    probs="{}",
                    scored_at=datetime.utcnow(),
                    escalated=False,
                    escalated_to=None,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def _override_get_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/risk/overall/1")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # Expect both axes present and tier forced to CRITICAL
    if (
        "confidentiality" not in data["axes"]
        or "integrity" not in data["axes"]
        or data["risk_tier"] != "CRITICAL"
    ):
        print("FAIL: response content mismatch", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)