# services/staged/axis_change_attributions/router.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_changes  # noqa: F401


router = APIRouter(prefix="/api")


class ChangeItem(BaseModel):
    server_id: int = Field(..., description="Identifier of the server")
    old_score: float = Field(..., description="Previous LLM axis score")
    new_score: float = Field(..., description="Current LLM axis score")
    delta: float = Field(..., description="Score difference (new - old)")
    reason: str = Field(..., description="Human‑readable reason for the change")


class AxisChangeResponse(BaseModel):
    changes: List[ChangeItem] = Field(..., description="List of score changes for the axis")


@router.get(
    "/axis/{axis_name}/changes",
    response_model=AxisChangeResponse,
    summary="Retrieve change attributions for a given axis",
)
def read_axis_changes(axis_name: str, db: Session = Depends(get_session)):
    """
    Return a list of change attributions for the specified axis.
    The heavy lifting is delegated to `services.staged.axis_change_attributions.logic.get_axis_changes`.
    """
    result = get_axis_changes(db, axis_name)
    if not result or not result.get("changes"):
        raise HTTPException(status_code=404, detail="No changes found for this axis")
    return result


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app and inject a dummy DB session.
    # The real DB session is overridden because the test runs in‑memory.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    # The endpoint does not actually use the session in this test,
    # so we provide a no‑op generator.
    def _dummy_session():
        yield None

    app.dependency_overrides[get_session] = _dummy_session
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Monkey‑patch the business logic to return a deterministic payload.
    # This avoids needing real database rows while still exercising the
    # router, response model, and HTTP handling.
    # ------------------------------------------------------------------- #
    logic_mod = importlib.import_module(
        "services.staged.axis_change_attributions.logic"
    )

    def _fake_get_axis_changes(_: Session, axis_name: str):
        return {
            "changes": [
                {
                    "server_id": 1,
                    "old_score": 0.45,
                    "new_score": 0.78,
                    "delta": 0.33,
                    "reason": f"Simulated change for axis {axis_name}",
                },
                {
                    "server_id": 2,
                    "old_score": 0.60,
                    "new_score": 0.55,
                    "delta": -0.05,
                    "reason": f"Simulated change for axis {axis_name}",
                },
            ]
        }

    logic_mod.get_axis_changes = _fake_get_axis_changes  # type: ignore

    # ------------------------------------------------------------------- #
    # Execute the request against the test client.
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/axis/test_axis/changes")
    if response.status_code == 200 and response.json().get("changes"):
        print("PASS")
    else:
        print("FAIL")