from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_summary

router = APIRouter(prefix="/api")


@router.get("/risk/summary")
def risk_summary(session: Session = Depends(get_session)):
    """Thin wrapper that delegates to the business‑logic layer."""
    return get_risk_summary(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed only when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session as app_get_session
    from . import logic

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app that includes this router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Override the DB dependency – the test does not hit a real database
    # ------------------------------------------------------------------- #
    def _dummy_session():
        """Placeholder that raises if the real DB is accessed."""
        raise RuntimeError("Database access is not expected in the self‑test")

    app.dependency_overrides[app_get_session] = _dummy_session

    # ------------------------------------------------------------------- #
    # Prepare a deterministic stub for the business logic
    # ------------------------------------------------------------------- #
    _expected_response = {
        "tiers": {"high": 2, "medium": 2, "low": 1},
        "axis_averages": {
            "p_top": {"avg_p_top": 0.6},
            "p_critical": {"avg_p_critical": 0.3},
            "p_danger": {"avg_p_danger": 0.1},
        },
        "total_servers": 5,
        "assessed_servers": 5,
    }

    def _stub_get_risk_summary(_session):
        return _expected_response

    # Patch the logic function used by the router
    logic.get_risk_summary = _stub_get_risk_summary

    # ------------------------------------------------------------------- #
    # Execute the request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/risk/summary")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    assert response.json() == _expected_response, "Response payload does not match expected"
    print("PASS")