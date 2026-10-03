from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_scoring_lifecycle

router = APIRouter(prefix="/api", tags=["scoring_lifecycle_tracker"])


@router.get("/scoring/lifecycle")
def scoring_lifecycle(
    days: int = Query(7, ge=1),
    session: Session = Depends(get_session),
):
    """
    Retrieve scoring lifecycle statistics for the past *days* days.
    """
    return get_scoring_lifecycle(session=session, days=days)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import CadenceJobRun

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed minimal data for the three scoring jobs
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    sample_runs = [
        CadenceJobRun(
            job="score_import",
            status="completed",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=30),
            rows_affected=1200,
            detail="imported scores",
        ),
        CadenceJobRun(
            job="score_finalize",
            status="completed",
            started_at=now - timedelta(hours=2, minutes=20),
            finished_at=now - timedelta(hours=2),
            rows_affected=1150,
            detail="finalized scores",
        ),
        CadenceJobRun(
            job="score_fire",
            status="failed",
            started_at=now - timedelta(hours=1, minutes=45),
            finished_at=now - timedelta(hours=1, minutes=30),
            rows_affected=0,
            detail="fire step error",
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(sample_runs)
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with router and overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate contract
    # ------------------------------------------------------------------- #
    response = client.get("/api/scoring/lifecycle?days=1")
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert isinstance(payload, dict), "Response payload must be a dict"
    assert "waves" in payload, "Missing 'waves' key in response"
    waves = payload["waves"]
    assert isinstance(waves, list) and len(waves) >= 3, "Expected at least three wave entries"
    # At least one entry must have a non‑null duration_sec
    assert any(w.get("duration_sec") is not None for w in waves), "No duration_sec values found"

    print("PASS")