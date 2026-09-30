"""services/staged/network_egress_scoring_consumer/router.py

Thin FastAPI router for the *network_egress_scoring_consumer* service.
All heavy‑lifting lives in ``services.staged.network_egress_scoring_consumer.logic``.
"""

from fastapi import APIRouter, Depends, Request, FastAPI
from fastapi.responses import JSONResponse
from app.db import get_session

# Import the real logic module – it contains the concrete implementations.
# The router simply forwards requests to those callables.
from . import logic

router = APIRouter()


@router.post("/process_scores")
async def process_scores_endpoint(request: Request, session=Depends(get_session)):
    """
    Forward the incoming JSON payload to ``logic.process_scores``.
    The logic function is expected to accept a ``session`` keyword argument
    and any additional keys from the request body.
    """
    payload = await request.json()
    return await logic.process_scores(session=session, **payload)


@router.get("/health")
async def health_endpoint():
    """Simple health‑check endpoint."""
    return JSONResponse(content={"status": "ok"})


# --------------------------------------------------------------------------- #
# Self‑test – executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/health")
    if resp.status_code == 200 and resp.json().get("status") == "ok":
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)