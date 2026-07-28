# services/staged/scoring_consumer/router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_scoring_consumer  # noqa: F401

router = APIRouter()


@router.get("/scoring/consumer")
def scoring_consumer_endpoint(server_id: int, db: Session = Depends(get_session)):
    """
    Retrieve scoring information for a given server.
    """
    result = get_scoring_consumer(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, McpLlmAxisScore

    # In‑memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    # Seed a single record
    with SessionLocal() as sess:
        sess.add(
            McpLlmAxisScore(
                server_id=1,
                axis1_label="Axis 1",
                axis1_p_top=0.9,
                axis2_label="Axis 2",
                axis2_p_top=0.8,
                axis3_label="Axis 3",
                axis3_p_top=0.7,
                axis4_label="Axis 4",
                axis4_p_top=0.6,
                axis5_label="Axis 5",
                axis5_p_top=0.5,
                axis6_label="Axis 6",
                axis6_p_top=0.4,
                overall_risk=0.55,
                criteria_version="v1",
                risk_tier="MEDIUM",
            )
        )
        sess.commit()

    # Override the dependency to use the in‑memory session
    def get_test_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/scoring/consumer?server_id=1")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic sanity checks
    assert "axes" in data, "Missing axes"
    assert isinstance(data["axes"], dict) and len(data["axes"]) == 6, "Axes count mismatch"
    assert "overall" in data, "Missing overall"
    assert "risk_tier" in data, "Missing risk_tier"
    assert "criteria_version" in data, "Missing criteria_version"

    print("PASS")