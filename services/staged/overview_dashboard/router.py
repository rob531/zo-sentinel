from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_overview_dashboard

router = APIRouter()


@router.get("/api/overview/dashboard")
def overview_dashboard(session: Session = Depends(get_session)):
    """Return the latest risk tier summary and trend."""
    return get_overview_dashboard(session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import date, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base, get_session as app_get_session
    from app.models import mcp_risk_tier_summary, mcp_risk_tier_trend

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB for the test)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed minimal data
    # ------------------------------------------------------------------- #
    db = SessionLocal()
    summary = mcp_risk_tier_summary(
        total_servers=10,
        tier_distribution=json.dumps({"low": 5, "medium": 3, "high": 2}),
    )
    db.add(summary)

    for i in range(3):
        trend = mcp_risk_tier_trend(
            date=date.today() - timedelta(days=i),
            tier="low",
            count=5 - i,
        )
        db.add(trend)

    db.commit()
    db.close()

    # ------------------------------------------------------------------- #
    # Dependency override
    # ------------------------------------------------------------------- #
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[app_get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # TestClient request
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/overview/dashboard")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    # ------------------------------------------------------------------- #
    # Basic shape validation
    # ------------------------------------------------------------------- #
    assert "summary" in payload and "trend" in payload, "Missing keys"
    assert payload["summary"]["total_servers"] == 10, "Incorrect total_servers"
    assert payload["trend"]["days"] == 3, "Incorrect days count"

    print("PASS")