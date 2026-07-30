from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import send_perspective_email_digest  # type: ignore


router = APIRouter(prefix="/api")


class DigestResponse(BaseModel):
    status: str
    message: str


@router.post("/perspective/digest", response_model=DigestResponse)
def perspective_digest(session: Session = Depends(get_session)):
    """
    Trigger the generation and sending of the perspective email digest.
    The underlying logic handles data retrieval, digest creation, and SMTP delivery.
    """
    result_message = send_perspective_email_digest(session)
    return DigestResponse(status="success", message=result_message)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    # Create a minimal FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)

    # Mock the SMTP interaction and the underlying logic to produce a predictable
    # message that includes a known membership change string.
    mock_message = "Digest generated – membership change: user@example.com added"

    with patch(
        "services.staged.perspective_email_digest.logic.send_perspective_email_digest",
        return_value=mock_message,
    ):
        client = TestClient(app)
        response = client.post("/api/perspective/digest")
        assert response.status_code == 200, f"Unexpected status {response.status_code}"
        data = response.json()
        assert data["status"] == "success", "Unexpected status field"
        assert (
            "membership change" in data["message"]
        ), "Digest message missing expected content"
        print("PASS")