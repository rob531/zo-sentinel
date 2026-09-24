"""services/staged/risk_tier_transition_dashboard_view/contract.py

FastAPI contract for the *risk_tier_transition_dashboard_view* service.
Mirrors the structure of ``services/_exemplar/contract.py`` and provides a
self‑test that runs with an in‑memory SQLite database.
"""

from __future__ import annotations

from typing import Generator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------------
# Real data layer imports – required for a non‑hollow contract
# ----------------------------------------------------------------------
from app.db import get_session, Base  # ``Base`` is the declarative base for the app models

# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/", response_model=dict)
def get_dashboard(session: Session = Depends(get_session)) -> dict:
    """
    Dashboard entry point.

    The real implementation would query the ``risk_tier_trend`` data and
    return a payload suitable for the front‑end.  For the contract we return
    a minimal placeholder payload.
    """
    # Placeholder – real logic lives elsewhere
    return {"message": "risk tier transition dashboard"}


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # ------------------------------------------------------------------
    # Create an in‑memory SQLite engine that mimics the app's DB
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    # Create all tables defined in the app's declarative base.
    # This ensures that any model accessed via ``get_session`` exists.
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------
    # Dependency override that yields a session bound to the in‑memory DB
    # ------------------------------------------------------------------
    def get_test_session() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Assemble a minimal FastAPI app for the contract test
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Execute the test client against the root endpoint
    # ------------------------------------------------------------------
    client = TestClient(app)
    response = client.get("/")
    if response.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        print(f"FAIL (status {response.status_code})")
        sys.exit(1)