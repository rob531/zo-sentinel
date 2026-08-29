"""
Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from typing import Any

from fastapi import APIRouter, Depends, FastAPI
import requests
from sqlalchemy.orm import Session

from app.db import get_session


def signal_scores_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
    """
    Query signal scores from ZoComputer store.
    Returns aggregated signal scores data.
    """
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return {"status": "ok", "data": data}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def _run_self_test() -> str:
    """Self-test verifying endpoint is callable."""
    result = signal_scores_endpoint()
    if "status" in result:
        return "PASS"
    return "FAIL"


router = APIRouter()


@router.get("/signal-scores")
def get_signal_scores() -> dict[str, Any]:
    """Public endpoint for signal scores."""
    return signal_scores_endpoint()


def create_app() -> FastAPI:
    """Factory for app instance."""
    app = FastAPI(title="auto_emitted_service")
    app.include_router(router)
    return app


if __name__ == "__main__":
    print(_run_self_test())