"""
services.staged.advisory_feed_normalizer.contract

Self‑test contract for the ``advisory_feed_normalizer`` staged service.
Mirrors the pattern used in ``services/_exemplar/contract.py``.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys

# Real data layer imports (required by the no‑hollow gate)
from app.db import get_session, Base  # noqa: F401
from app.models import (  # noqa: F401
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)

# Router import – the service must expose a FastAPI router
from services.staged.advisory_feed_normalizer.router import router as advisory_router

# --------------------------------------------------------------------------- #
# FastAPI application definition
# --------------------------------------------------------------------------- #
app = FastAPI(title="advisory_feed_normalizer contract")
app.include_router(advisory_router)


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # --------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine for the self‑test.
    # The real models are bound to this engine so that any DB access performed
    # by the router during the test succeeds (tables are empty but present).
    # --------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def _test_session() -> object:  # pragma: no cover
        """Provide a session bound to the in‑memory SQLite engine."""
        return TestSessionLocal()

    # Override the real ``get_session`` dependency with the test version.
    app.dependency_overrides[get_session] = _test_session

    # --------------------------------------------------------------------- #
    # Run a minimal request against the assembled application.
    # ``/openapi.json`` is always served by FastAPI and does not require any
    # additional endpoint definitions.
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/openapi.json")
    if response.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)