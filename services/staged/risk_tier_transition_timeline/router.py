from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_timeline

router = APIRouter(prefix="/api")


@router.get("/risk/tier_transition_timeline")
def tier_transition_timeline(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    """Return a chronological series of risk‑tier counts for the past *days*."""
    return get_timeline(session, days)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app and include the router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # In‑memory SQLite DB for the test
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables using the declarative base from the real app
    from app.db import Base  # noqa: E402
    Base.metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed sample data (two servers, three dates)
    # ------------------------------------------------------------------- #
    session = SessionLocal()
    from app.models import McpLlmAxisScore, McpServerRegistry  # noqa: E402

    today = datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    dates = [today - datetime.timedelta(days=i) for i in range(3)]

    # Server registry
    session.add_all(
        [
            McpServerRegistry(server_id=1, risk_tier="LOW"),
            McpServerRegistry(server_id=2, risk_tier="HIGH"),
        ]
    )

    # Axis scores (overall_risk) for each server on each date
    for d in dates:
        session.add_all(
            [
                McpLlmAxisScore(
                    server_id=1,
                    axis_name="overall_risk",
                    p_top=0.5,
                    scored_at=d,
                ),
                McpLlmAxisScore(
                    server_id=2,
                    axis_name="overall_risk",
                    p_top=0.7,
                    scored_at=d,
                ),
            ]
        )
    session.commit()
    session.close()

    # ------------------------------------------------------------------- #
    # Override the DB dependency to use the in‑memory session
    # ------------------------------------------------------------------- #
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # ------------------------------------------------------------------- #
    # Exercise the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/risk/tier_transition_timeline?days=3")
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 3
    assert len(payload["timeline"]) == 3
    for day_entry in payload["timeline"]:
        counts = day_entry["tier_counts"]
        assert counts["LOW"] == 1
        assert counts["HIGH"] == 1
        assert counts["MEDIUM"] == 0
        assert counts["CRITICAL"] == 0
    print("PASS")