"""services/staged/api_key_rotation/router.py

Thin FastAPI router exposing API key rotation and revocation endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

# Real application dependencies
from app.db import get_session
from app.models import ApiKey, Org  # noqa: F401  (imported for side‑effects / type checking)

# Logic layer – may be implemented elsewhere; fallback to a stub if missing.
try:
    from .logic import rotate_key, revoke_key  # type: ignore
except Exception:  # pragma: no cover
    def rotate_key(session, org_id: int, label: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("rotate_key logic not implemented")

    def revoke_key(session, org_id: int, key_id: int) -> Dict[str, Any]:
        raise NotImplementedError("revoke_key logic not implemented")

router = APIRouter()


class RotateRequest(BaseModel):
    org_id: int
    label: Optional[str] = None


class RevokeRequest(BaseModel):
    org_id: int
    key_id: int


@router.post("/api/keys/rotate", response_model=Dict[str, Any])
def rotate_api_key(
    payload: RotateRequest,
    session=Depends(get_session),
):
    """
    Rotate (create) a new API key for an organisation.
    """
    try:
        result = rotate_key(session, payload.org_id, payload.label)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/api/keys/revoke", response_model=Dict[str, Any])
def revoke_api_key(
    payload: RevokeRequest,
    session=Depends(get_session),
):
    """
    Revoke an existing API key for an organisation.
    """
    try:
        result = revoke_key(session, payload.org_id, payload.key_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app and inject test overrides
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the DB session – the test logic does not need a real DB.
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    # Monkey‑patch the logic functions with deterministic test versions.
    def _test_rotate_key(session, org_id: int, label: Optional[str] = None):
        return {
            "success": True,
            "message": "API key rotated",
            "new_key": "testkey-12345",
            "org_id": org_id,
            "label": label,
        }

    def _test_revoke_key(session, org_id: int, key_id: int):
        return {
            "success": True,
            "message": f"API key {key_id} revoked",
            "org_id": org_id,
            "key_id": key_id,
        }

    # Apply monkey‑patches
    globals()["rotate_key"] = _test_rotate_key
    globals()["revoke_key"] = _test_revoke_key

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Test /api/keys/rotate
    # ------------------------------------------------------------------- #
    rotate_resp = client.post(
        "/api/keys/rotate",
        json={"org_id": 1, "label": "unit-test"},
    )
    assert rotate_resp.status_code == 200, f"Rotate status {rotate_resp.status_code}"
    rotate_json = rotate_resp.json()
    assert rotate_json.get("success") is True, "Rotate success flag"
    assert "new_key" in rotate_json, "Rotate returned new_key"

    # ------------------------------------------------------------------- #
    # Test /api/keys/revoke
    # ------------------------------------------------------------------- #
    revoke_resp = client.post(
        "/api/keys/revoke",
        json={"org_id": 1, "key_id": 42},
    )
    assert revoke_resp.status_code == 200, f"Revoke status {revoke_resp.status_code}"
    revoke_json = revoke_resp.json()
    assert revoke_json.get("success") is True, "Revoke success flag"

    print("PASS")