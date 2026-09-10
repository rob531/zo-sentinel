"""auto_emitted_service package entry point.

Provides FastAPI application and endpoints used across the project.
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
import requests

# App database session and models – required for all imports that depend on this module.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

app = FastAPI()


@app.get("/signal-scores")
def signal_scores_endpoint(db=Depends(get_session)):
    """Fetch signal scores from the ZoComputer store.

    The query is sent to the write‑service HTTP endpoint; the result is returned
    as‑is to the caller.
    """
    try:
        # The query string is static – no user‑controlled concatenation → safe from SQL‑i.
        payload = {"query": "SELECT * FROM mcp_signal_scores"}
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)
        resp.raise_for_status()
        return JSONResponse(content=resp.json())
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running `python -m auto_emitted_service`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Dependency override: provide a dummy DB session that does nothing.
    # ------------------------------------------------------------------- #
    def _dummy_session():
        class _Dummy:
            def __enter__(self):  # pragma: no cover
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):  # pragma: no cover
                pass

        return _Dummy()

    app.dependency_overrides[get_session] = _dummy_session

    # ------------------------------------------------------------------- #
    # Mock external HTTP call to avoid real network access.
    # ------------------------------------------------------------------- #
    _original_post = requests.post

    def _mock_post(url, json, timeout=None):
        class _MockResponse:
            def raise_for_status(self):  # pragma: no cover
                pass

            def json(self):
                return {"mocked": "signal_scores"}

        return _MockResponse()

    requests.post = _mock_post

    # ------------------------------------------------------------------- #
    # Execute test client request.
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/signal-scores")

    # Restore original function.
    requests.post = _original_post

    # ------------------------------------------------------------------- #
    # Outcome.
    # ------------------------------------------------------------------- #
    if response.status_code == 200:
        print("PASS")
    else:
        print("FAIL")