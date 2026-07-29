"""
services.staged.risk_tier_trend_analysis.contract
-------------------------------------------------

FastAPI contract for the *risk_tier_trend_analysis* service.

The module:
* imports the real data layer (app.db, app.models)
* exposes the router defined in ``router.py``
* provides a ``create_app`` helper for normal operation
* contains a self‑test runnable with ``python -m …`` that:
    - overrides the ``get_session`` dependency with an in‑memory SQLite DB
    - creates the required tables
    - seeds five servers with tier changes over three days
    - calls the endpoint ``GET /api/risk/trend/analysis?days=N``
    - asserts a 200 response and that at least one pattern is reported
    - prints ``PASS`` on success
"""

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

# ----------------------------------------------------------------------
# Real application imports – must stay untouched for production use
# ----------------------------------------------------------------------
from app.db import get_session, Base  # noqa: F401
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401

# ----------------------------------------------------------------------
# Service specific imports
# ----------------------------------------------------------------------
from .router import router as analysis_router

# ----------------------------------------------------------------------
# FastAPI application factory
# ----------------------------------------------------------------------
def create_app() -> FastAPI:
    """Create a FastAPI app that includes the risk‑tier‑trend‑analysis router."""
    app = FastAPI()
    app.include_router(analysis_router)
    return app


# ----------------------------------------------------------------------
# Self‑test (executed with ``python -m services.staged.risk_tier_trend_analysis.contract``)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # ------------------------------------------------------------------
    # 1️⃣  Build an in‑memory SQLite engine and bind a test session factory
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def get_test_session() -> Session:  # pragma: no cover
        """Dependency override that yields a fresh SQLite session."""
        return TestSessionLocal()

    # ------------------------------------------------------------------
    # 2️⃣  Create the FastAPI app and inject the test session dependency
    # ------------------------------------------------------------------
    app = create_app()
    app.dependency_overrides[get_session] = get_test_session
    client = TestClient(app)

    # ------------------------------------------------------------------
    # 3️⃣  Seed minimal data required by the service logic
    # ------------------------------------------------------------------
    from datetime import datetime, timedelta

    with TestSessionLocal() as db:
        # ---- servers ----------------------------------------------------
        server_rows = [
            {"id": i + 1, "name": f"server{i + 1}"}
            for i in range(5)
        ]
        db.execute(insert(McpServerRegistry.__table__).values(server_rows))

        # ---- scores (tier changes) ------------------------------------
        base_ts = datetime.utcnow()
        # deterministic tier pattern for the test: server 1‑5 have tiers 1‑5 on day 0,
        # then shift down by one each subsequent day.
        tier_matrix = [
            [1, 2, 3, 4, 5],  # day 0
            [2, 3, 4, 5, 1],  # day 1
            [3, 4, 5, 1, 2],  # day 2
        ]

        score_rows = []
        for day_offset, day_tiers in enumerate(tier_matrix):
            ts = base_ts - timedelta(days=day_offset)
            for server_id, tier in zip(range(1, 6), day_tiers):
                score_rows.append(
                    {
                        "server_id": server_id,
                        "risk_tier": tier,
                        "timestamp": ts,
                    }
                )
        db.execute(insert(McpLlmAxisScore.__table__).values(score_rows))
        db.commit()

    # ------------------------------------------------------------------
    # 4️⃣  Exercise the endpoint
    # ------------------------------------------------------------------
    resp = client.get("/api/risk/trend/analysis?days=3")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()

    # ------------------------------------------------------------------
    # 5️⃣  Verify that at least one pattern was detected
    # ------------------------------------------------------------------
    analysis = payload.get("analysis", {})
    patterns = analysis.get("patterns", [])
    assert isinstance(patterns, list), "patterns field is not a list"
    assert len(patterns) > 0, "expected at least one pattern in the analysis"
    print("PASS")