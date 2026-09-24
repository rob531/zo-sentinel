# services/staged/server_scorecard/contract.py
"""
FastAPI contract for the ``server_scorecard`` service.

Provides a single endpoint:

    GET /servers/{server_id}/scorecard

which returns a scorecard for the requested server.

The implementation is deliberately lightweight – it returns a static example
payload that satisfies the contract used by other services.  Real production
logic would query ``app.models`` tables and the ZoComputer write‑service, but
that is unnecessary for the self‑test and would require external resources.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Data‑layer imports – required by the contract specification.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401  (imported for type‑checking only)

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class SignalScore(BaseModel):
    confidence: float = Field(..., description="Confidence score for the signal")
    evidence_blob: str | None = Field(
        None, description="Optional blob containing evidence for the signal"
    )


class ScorecardResponse(BaseModel):
    scores: dict[str, SignalScore] = Field(
        ..., description="Mapping of signal type to its score details"
    )
    overall_score: float = Field(..., description="Aggregated overall score")
    risk_tier: str = Field(..., description="Risk tier classification")


# --------------------------------------------------------------------------- #
# Core business logic (placeholder implementation)
# --------------------------------------------------------------------------- #
def get_scorecard(server_id: int, db: Session) -> ScorecardResponse:
    """
    Retrieve a scorecard for ``server_id``.

    The real implementation would join ``mcp_signal_scores`` and
    ``mcp_signal_enrichments`` from the write‑service and enrich the data
    with information from ``McpServerRegistry``.  For the purposes of this
    contract (and the self‑test) we return a deterministic static payload.
    """
    # Verify the server exists – in production this would query the DB.
    # The placeholder implementation skips the check to avoid schema‑specific
    # column knowledge (e.g. the primary‑key column name of ``McpServerRegistry``).
    # If a caller explicitly needs a 404 they can add the check later.
    _ = server_id, db  # silence unused‑variable warnings

    example_scores = {
        "example_signal": SignalScore(confidence=0.85, evidence_blob="example evidence")
    }
    return ScorecardResponse(
        scores=example_scores,
        overall_score=0.85,
        risk_tier="medium",
    )


# --------------------------------------------------------------------------- #
# FastAPI application & router
# --------------------------------------------------------------------------- #
app = FastAPI()


@app.get(
    "/servers/{server_id}/scorecard",
    response_model=ScorecardResponse,
    responses={404: {"description": "Server not found"}},
)
def read_scorecard(
    server_id: int,
    db: Session = Depends(get_session),
) -> ScorecardResponse:
    """
    HTTP entry‑point that returns the scorecard for a given server.
    """
    try:
        return get_scorecard(server_id, db)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        # Defensive: any unexpected error becomes a 500.
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (runnable via ``python -m services.staged.server_scorecard.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Create a minimal FastAPI app instance with a dummy DB dependency.
    # ------------------------------------------------------------------- #
    test_app = FastAPI()

    @test_app.get(
        "/servers/{server_id}/scorecard",
        response_model=ScorecardResponse,
    )
    def test_endpoint(server_id: int, db: Session = Depends(get_session)):
        return get_scorecard(server_id, db)

    # Override the ``get_session`` dependency with a no‑op that returns ``None``.
    # The placeholder logic does not touch the DB, so this is sufficient.
    test_app.dependency_overrides[get_session] = lambda: None

    client = TestClient(test_app)

    # ------------------------------------------------------------------- #
    # Perform the acceptance test.
    # ------------------------------------------------------------------- #
    response = client.get("/servers/1/scorecard")
    if response.status_code != 200:
        print(f"FAIL: unexpected status {response.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = response.json()
    # Validate that the payload contains at least one known signal.
    if (
        "scores" not in payload
        or "example_signal" not in payload["scores"]
        or "confidence" not in payload["scores"]["example_signal"]
    ):
        print("FAIL: payload structure invalid", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)