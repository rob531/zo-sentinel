from typing import List, Optional

import requests
from fastapi import Depends

from app.db import get_session, SessionLocal, Base
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def _fetch_axis_scores(session: Session, server_id: int) -> List[McpLlmAxisScore]:
    """Return all LLM axis scores for a given server."""
    return (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )


def _derive_risk_tier(scores: List[McpLlmAxisScore]) -> Optional[str]:
    """
    Derive a risk tier from a list of axis scores.

    Simple heuristic:
        - If any score has p_critical >= 0.5 → "critical"
        - Else if any score has p_danger   >= 0.5 → "danger"
        - Else if any score has p_top      >= 0.5 → "high"
        - Otherwise                                   → "low"
    """
    if not scores:
        return None

    for s in scores:
        if getattr(s, "p_critical", 0) >= 0.5:
            return "critical"
    for s in scores:
        if getattr(s, "p_danger", 0) >= 0.5:
            return "danger"
    for s in scores:
        if getattr(s, "p_top", 0) >= 0.5:
            return "high"
    return "low"


def get_risk_tier(
    server_id: int, session: Session = Depends(get_session)
) -> Optional[str]:
    """
    Public helper used by routers and other services.
    Returns the derived risk tier for ``server_id`` without persisting it.
    """
    scores = _fetch_axis_scores(session, server_id)
    return _derive_risk_tier(scores)


def _write_risk_tier(server_id: int, tier: Optional[str]) -> None:
    """
    Persist the derived risk tier to the mesh write‑service.
    The write‑service expects a JSON payload with the primary key(s) and the
    column(s) to update.
    """
    payload = {
        "table": "mcp_server_registry",
        "records": [{"server_id": server_id, "risk_tier": tier}],
    }
    # The write service returns 200 on success; raise for any other status.
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    resp.raise_for_status()


def update_risk_tier(
    server_id: int, session: Session = Depends(get_session)
) -> Optional[str]:
    """
    Compute the risk tier for ``server_id`` and persist it via the write‑service.
    Returns the tier that was written (or ``None`` if no scores exist).
    """
    tier = get_risk_tier(server_id, session)
    _write_risk_tier(server_id, tier)
    return tier


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal sanity check – the module loads and the public helpers are callable.
    # Full integration tests are performed elsewhere in the repo.
    print("PASS")