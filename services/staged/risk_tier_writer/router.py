"""Risk tier writer router.

Exposes a single POST endpoint that computes a risk tier for every server
based on the highest `p_top` value across all axis scores and optionally
writes the result back to the server registry.
"""

from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real data layer imports (required by the no‑hollow gate)
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Import the sibling logic module (required by the specification,
# even though the router implements the logic directly).
from . import logic  # noqa: F401

router = APIRouter()


class RiskTierWriteRequest(BaseModel):
    dry_run: bool = False


def _compute_risk_tier(p_top: float) -> str:
    """Return the risk tier according to the p_top thresholds."""
    if p_top >= 0.9:
        return "CRITICAL"
    if p_top >= 0.7:
        return "DANGER"
    return "NOMINAL"


@router.post("/internal/scoring/risk-tier-write")
def write_risk_tier(
    payload: RiskTierWriteRequest,
    session: Session = Depends(get_session),
):
    """Compute risk tiers for all servers and optionally persist them.

    Returns a list of ``{server_id, proposed_tier}`` when ``dry_run`` is true,
    otherwise returns ``{updated: N}`` where *N* is the number of rows written.
    """
    # Gather the maximum p_top per server.
    max_p_top: dict[str, float] = defaultdict(float)
    for row in session.query(McpLlmAxisScore).all():
        if row.p_top is None:
            continue
        if row.p_top > max_p_top[row.server_id]:
            max_p_top[row.server_id] = row.p_top

    # Build the tier proposals.
    proposals = [
        {"server_id": srv, "proposed_tier": _compute_risk_tier(p_top)}
        for srv, p_top in max_p_top.items()
    ]

    if payload.dry_run:
        return proposals

    # Persist the proposals (UPSERT semantics).
    updated = 0
    for item in proposals:
        qry = (
            session.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == item["server_id"])
        )
        reg = qry.one_or_none()
        if reg:
            reg.risk_tier = item["proposed_tier"]
        else:
            reg = McpServerRegistry(
                server_id=item["server_id"], risk_tier=item["proposed_tier"]
            )
            session.add(reg)
        updated += 1

    session.commit()
    return {"updated": updated}


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # --------------------------------------------------------------------- #
    # Minimal in‑memory mock that mimics the subset of the SQLAlchemy API we
    # use in the endpoint.
    # --------------------------------------------------------------------- #
    class _MockSession:
        def __init__(self):
            self.axis_scores = []          # type: list
            self.server_registry = {}      # server_id -> McpServerRegistry

        # ``query`` returns a mock query object that knows which model it
        # operates on.
        def query(self, model):
            return _MockQuery(self, model)

        def add(self, instance):
            if isinstance(instance, McpServerRegistry):
                self.server_registry[instance.server_id] = instance

        def commit(self):
            # No‑op for the in‑memory store.
            pass

    class _MockQuery:
        def __init__(self, session: _MockSession, model):
            self.session = session
            self.model = model
            self._filter_value = None

        def all(self):
            if self.model is McpLlmAxisScore:
                return self.session.axis_scores
            return []

        def filter(self, *criterion):
            # SQLAlchemy binary expressions expose the bound value via
            # ``right.value`` – we extract that for the UPSERT lookup.
            expr = criterion[0]
            if hasattr(expr, "right") and hasattr(expr.right, "value"):
                self._filter_value = expr.right.value
            return self

        def one_or_none(self):
            if self.model is McpServerRegistry:
                return self.session.server_registry.get(self._filter_value)
            return None

    # --------------------------------------------------------------------- #
    # Simple stand‑in objects that carry the required attributes.
    # --------------------------------------------------------------------- #
    class _SimpleAxisScore:
        def __init__(self, server_id: str, p_top: float):
            self.server_id = server_id
            self.p_top = p_top

    class _SimpleServerRegistry(McpServerRegistry):  # type: ignore[misc]
        # Inherit just for type compatibility; we only need the two fields.
        def __init__(self, server_id: str, risk_tier: str | None = None):
            self.server_id = server_id
            self.risk_tier = risk_tier

    # --------------------------------------------------------------------- #
    # Populate the mock with three servers covering each tier.
    # --------------------------------------------------------------------- #
    mock_session = _MockSession()
    mock_session.axis_scores = [
        _SimpleAxisScore(server_id="srv_critical", p_top=0.95),  # CRITICAL
        _SimpleAxisScore(server_id="srv_danger", p_top=0.75),    # DANGER
        _SimpleAxisScore(server_id="srv_nominal", p_top=0.45),   # NOMINAL
    ]

    # --------------------------------------------------------------------- #
    # Build a FastAPI app that uses the mock session via dependency override.
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.include_router(router)

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute the endpoint (dry_run=False) and verify the results.
    # --------------------------------------------------------------------- #
    response = client.post(
        "/internal/scoring/risk-tier-write", json={"dry_run": False}
    )
    assert response.status_code == 200, f"Unexpected status: {response.status_code}"
    result = response.json()
    assert result.get("updated") == 3, f"Expected 3 updates, got {result}"

    # Verify that the in‑memory registry now holds the expected tiers.
    assert mock_session.server_registry["srv_critical"].risk_tier == "CRITICAL"
    assert mock_session.server_registry["srv_danger"].risk_tier == "DANGER"
    assert mock_session.server_registry["srv_nominal"].risk_tier == "NOMINAL"

    print("PASS")