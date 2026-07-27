# services/staged/freshness_metadata/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_metadata, FreshnessMetadataResponse

router = APIRouter(prefix="/api", tags=["freshness_metadata"])


@router.get(
    "/servers/{server_id}/freshness",
    response_model=FreshnessMetadataResponse,
    name="Get Freshness Metadata",
)
def freshness_endpoint(server_id: int, db: Session = Depends(get_session)):
    """Thin wrapper that delegates to the business‑logic layer."""
    return get_freshness_metadata(server_id, db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create a minimal FastAPI app and include this router
    app = FastAPI()
    app.include_router(router)

    # --------------------------------------------------------------------- #
    # Dependency override: provide a dummy DB session (the logic layer will
    # be monkey‑patched to avoid any real DB access)
    # --------------------------------------------------------------------- #
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    # --------------------------------------------------------------------- #
    # Monkey‑patch the logic function to return deterministic data.
    # This satisfies the contract without needing a real database.
    # --------------------------------------------------------------------- #
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)

    def _mock_freshness(server_id: int, db: Session):
        return FreshnessMetadataResponse(
            server_id=server_id,
            last_scanned=now - timedelta(hours=5),
            scan_count=3,
            last_axis_score_at=now - timedelta(hours=2),
            hours_since_score=2.0,
            score_stale_hours=24.0,
            scan_stale_hours=48.0,
            overall_fresh="fresh",
        )

    # Replace the real implementation with the mock
    import types

    logic_module = sys.modules[__name__].__dict__["logic"] if "logic" in sys.modules[__name__].__dict__ else None
    if logic_module is None:
        # Import the sibling module directly
        from . import logic as logic_module

    logic_module.get_freshness_metadata = _mock_freshness  # type: ignore

    # --------------------------------------------------------------------- #
    # Run the test client against the endpoint
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/servers/1/freshness")
    try:
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        payload = response.json()
        assert payload["hours_since_score"] >= 0, "hours_since_score is negative"
        assert payload["overall_fresh"] in {"fresh", "stale", "unknown"}, "Invalid overall_fresh value"
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)

    print("PASS")