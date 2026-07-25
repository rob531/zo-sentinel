from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session
from typing import Dict

from app.db import get_session
from app.models import McpRiskRegister, Base

router = APIRouter()


@router.get(
    "/risk_tier_thresholds",
    response_model=Dict[str, float],
    summary="Current risk tier thresholds",
)
def get_risk_tier_thresholds(db: Session = Depends(get_session)):
    """Return a mapping of risk tier name to its threshold value."""
    rows = db.query(McpRiskRegister).all()
    return {row.tier: float(row.threshold) for row in rows}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Expected data
    EXPECTED = {
        "TRUSTED_GENERAL": 0.1,
        "TRUSTED_RESEARCH": 0.2,
        "ENTERPRISE_CONTROLLED": 0.3,
        "CAUTION_LIMITED": 0.4,
        "HIGH_RISK_ISOLATED": 0.5,
        "KNOWN_THREAT": 0.6,
    }

    # In‑memory SQLite setup
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Populate test data
    with TestSession() as sess:
        for tier, thresh in EXPECTED.items():
            sess.add(McpRiskRegister(tier=tier, threshold=thresh))
        sess.commit()

    # Dependency override
    def get_test_session() -> Session:  # type: ignore
        with TestSession() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/risk_tier_thresholds")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data != EXPECTED:
        print(f"FAIL: Unexpected payload {data}", file=sys.stderr)
        sys.exit(1)

    print("PASS")