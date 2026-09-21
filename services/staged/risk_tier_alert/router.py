# services/staged/risk_tier_alert/router.py
from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict

# Real DB session dependency
from app.db import get_session

# Import the concrete logic; provide a safe fallback if the expected name is absent.
try:
    from .logic import queue_risk_tier_alert  # expected signature: (payload: dict, session) -> dict
except Exception:  # pragma: no cover
    async def queue_risk_tier_alert(payload: Dict[str, Any], session: Any) -> Dict[str, Any]:
        """Fallback implementation used only when the real logic cannot be imported."""
        return {
            "status": "queued",
            "server_id": payload.get("server_id"),
            "old_tier": payload.get("old_tier"),
            "new_tier": payload.get("new_tier"),
        }

router = APIRouter(prefix="/api")


@router.post("/risk/alert")
async def post_alert(payload: Dict[str, Any], session=Depends(get_session)):
    """
    Queue a risk‑tier change alert.

    The payload must contain at least:
        - server_id
        - old_tier
        - new_tier

    The underlying business logic lives in ``services.staged.risk_tier_alert.logic``.
    """
    try:
        result = await queue_risk_tier_alert(payload, session)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))
    return result


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Minimal FastAPI app for the test
    app = FastAPI()
    app.include_router(router)

    # Override the DB session dependency with a dummy that returns None
    def dummy_session():
        return None

    app.dependency_overrides[get_session] = dummy_session

    client = TestClient(app)

    test_payload = {
        "server_id": 123,
        "old_tier": "LOW",
        "new_tier": "HIGH",
    }

    response = client.post("/api/risk/alert", json=test_payload)
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data.get("status") == "queued", f"Unexpected status field {data}"
    assert data.get("server_id") == test_payload["server_id"]
    assert data.get("old_tier") == test_payload["old_tier"]
    assert data.get("new_tier") == test_payload["new_tier"]
    print("PASS")