# deps: fastapi, pydantic, sqlalchemy
"""Circuit Breaker Status API

Provides an endpoint to retrieve the overall risk (circuit breaker) status for a given server.

Endpoints:
  GET /api/circuit_breaker_status/{server_id}
    Returns the latest overall risk label and escalation flag for the server.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# Import the shared DB session dependency and ORM models
from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["circuit_breaker_status_api"])


class CircuitBreakerStatusResponse(BaseModel):
    server_id: str = Field(..., description="Identifier of the server")
    overall_risk_label: str = Field(..., description="Risk label for the overall axis")
    escalated: bool = Field(..., description="Whether the server is escalated")
    scored_at: datetime = Field(..., description="Timestamp of the score record")


@router.get("/circuit_breaker_status/{server_id}", response_model=CircuitBreakerStatusResponse)
def get_circuit_breaker_status(
    server_id: str,
    db=Depends(get_session),
):
    """Return the latest overall risk status for a server.

    The function queries the ``McpLlmAxisScore`` table for the most recent row
    where ``axis_name`` is ``overall_risk`` for the given ``server_id``.
    """
    score = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")
    return CircuitBreakerStatusResponse(
        server_id=server_id,
        overall_risk_label=score.label,
        escalated=bool(score.escalated),
        scored_at=score.scored_at,
    )


# ---------------------------------------------------------------------------
# Self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    try:
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from app.main import app
    except ModuleNotFoundError as e:
        print(f"SKIP: {e}")
        sys.exit(0)

    from app.db import get_session

    # ── mock score row ────────────────────────────────────────────────────────
    class MockScore:
        def __init__(self, server_id: str, label: str, escalated: bool, scored_at: datetime):
            self.server_id = server_id
            self.label = label
            self.escalated = escalated
            self.scored_at = scored_at
            self.axis_name = "overall_risk"

    class MockQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *criteria):
            return self

        def order_by(self, *args):
            return self

        def first(self):
            return self._rows[0] if self._rows else None

    class MockSessionSuccess:
        def query(self, model):
            return MockQuery([
                MockScore(
                    server_id="srv-123",
                    label="low",
                    escalated=False,
                    scored_at=datetime.utcnow(),
                )
            ])

    class MockSessionEmpty:
        def query(self, model):
            return MockQuery([])

    # ── success case ──────────────────────────────────────────────────────────
    app.dependency_overrides[get_session] = MockSessionSuccess
    client = TestClient(app)
    resp = client.get("/api/circuit_breaker_status/srv-123")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if data.get("server_id") != "srv-123" or data.get("overall_risk_label") != "low":
        print(f"FAIL: unexpected response data {data}")
        sys.exit(1)

    # ── 404 case ──────────────────────────────────────────────────────────────
    app.dependency_overrides[get_session] = MockSessionEmpty
    resp = client.get("/api/circuit_breaker_status/unknown")
    if resp.status_code != 404:
        print(f"FAIL: expected 404, got {resp.status_code}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
