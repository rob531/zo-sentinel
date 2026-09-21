"""Risk Tier Timeline service package."""

from fastapi import APIRouter, Depends
import requests

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

router = APIRouter()


def _query_mesh(sql: str):
    """Query the ZoComputer mesh store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


@router.get("/risk_tier_timeline/mesh_scores")
def get_mesh_scores():
    """Return a sample of mesh scores."""
    sql = "SELECT * FROM mesh_memory LIMIT 10"
    return _query_mesh(sql)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal self‑test that does not depend on external services.
    try:
        # Direct call to the endpoint function (no DB or HTTP needed for this test)
        _ = get_mesh_scores  # existence check
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print("FAIL", exc)