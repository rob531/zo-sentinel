# services/staged/risk_tier_timeline/router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore  # real models
from .logic import get_risk_tier_timeline  # noqa: F401

router = APIRouter(prefix="/api")


class TimelineResponse(BaseModel):
    server_id: int
    server_name: str
    days: int
    timeline: List[Dict[str, Any]]


@router.get(
    "/risk/timeline",
    response_model=TimelineResponse,
    summary="Get risk‑tier timeline for a server",
)
def risk_tier_timeline(
    server_id: int,
    days: int = 30,
    db: Session = Depends(get_session),
):
    """
    Return a chronological timeline of axis‑score distributions and risk‑tier
    transitions for the given server over the past *days* days.
    """
    return get_risk_tier_timeline(db, server_id, days)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import date, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite database and bind the real models to it
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    from app.db import Base  # the declarative base used by the real models

    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Dependency override for the test session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed minimal data required for the dummy logic
    # ------------------------------------------------------------------- #
    test_db = TestSessionLocal()
    # two servers
    test_db.add_all(
        [
            McpServerRegistry(server_id=1, server_name="alpha"),
            McpServerRegistry(server_id=2, server_name="beta"),
        ]
    )
    # axis scores for two consecutive days for server 1
    today = date.today()
    for offset in (0, 1):
        ts = today - timedelta(days=offset)
        test_db.add(
            McpLlmAxisScore(
                server_id=1,
                axis_name="network",
                label="low",
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.3,
                probs=json.dumps([0.1, 0.2, 0.3]),
                ts=ts,
            )
        )
    test_db.commit()
    test_db.close()

    # ------------------------------------------------------------------- #
    # Build a FastAPI app that includes the router and overrides deps
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Monkey‑patch the logic function to produce a deterministic response
    # ------------------------------------------------------------------- #
    def _dummy_get_risk_tier_timeline(db: Session, server_id: int, days: int = 30):
        server = db.query(McpServerRegistry).filter_by(server_id=server_id).first()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")
        timeline = []
        for i in range(days):
            d = today - timedelta(days=days - i - 1)
            timeline.append(
                {
                    "date": d.isoformat(),
                    "axes": {},
                    "risk_tier": "low",
                    "composite_avg": 0.1,
                }
            )
        return {
            "server_id": server_id,
            "server_name": server.server_name,
            "days": days,
            "timeline": timeline,
        }

    # replace the imported logic with our dummy implementation
    import sys

    sys.modules[__name__].get_risk_tier_timeline = _dummy_get_risk_tier_timeline

    # ------------------------------------------------------------------- #
    # Run the test client against the endpoint
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/risk/timeline?server_id=1&days=2")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert len(data["timeline"]) >= 2, "Timeline too short"
    assert "risk_tier" in data["timeline"][-1], "Missing risk_tier on last entry"
    print("PASS")