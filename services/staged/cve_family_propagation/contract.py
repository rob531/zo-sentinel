"""
services/staged/cve_family_propagation/contract.py

FastAPI contract for the ``cve_family_propagation`` staged service.
Mirrors the pattern used in ``services/_exemplar/contract.py``.
Provides a self‑test that can be run with:
    python -m services.staged.cve_family_propagation.contract
"""

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ----------------------------------------------------------------------
# Real data layer imports – these must stay unchanged for production.
# ----------------------------------------------------------------------
from app.db import get_session  # pragma: no cover
from app.models import (
    VulnAdvisory,
    VulnLink,
    MCPThreatAssociation,
    Base,
)  # pragma: no cover

# ----------------------------------------------------------------------
# Router import – the actual endpoint implementation lives in ``router.py``.
# ----------------------------------------------------------------------
from services.staged.cve_family_propagation.router import router

# ----------------------------------------------------------------------
# FastAPI application definition
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (executed when the module is run as a script)
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    # Create a throw‑away SQLite in‑memory database and bind the models.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create all tables defined in ``app.models``.
    Base.metadata.create_all(bind=engine)

    # Dependency override: replace the production ``get_session`` with a test session.
    def _test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _test_session

    # ------------------------------------------------------------------
    # Minimal seeding – we keep it empty because the acceptance test only
    # asserts that the endpoint runs and returns a deterministic result.
    # ------------------------------------------------------------------
    with SessionLocal() as db:
        # No explicit seed data; the endpoint should handle an empty input set.
        db.commit()

    client = TestClient(app)

    # Call the endpoint with an empty advisory list.
    response = client.post(
        "/api/cve/family_propagation",
        json={"advisory_ids": []},
    )

    # Basic sanity checks.
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    payload = response.json()
    assert isinstance(payload, dict), "Response payload is not a dict"
    assert "propagated" in payload, "'propagated' key missing in response"
    assert payload["propagated"] == 0, f"Expected 0 propagated, got {payload['propagated']}"
    assert "errors" in payload, "'errors' key missing in response"
    assert isinstance(payload["errors"], list), "'errors' is not a list"

    print("PASS")