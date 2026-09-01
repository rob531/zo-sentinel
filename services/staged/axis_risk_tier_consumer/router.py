from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import sync_risk_tiers

router = APIRouter(prefix="/api", tags=["axis_risk_tier_consumer"])


@router.get("/scoring/tier-sync")
def tier_sync(session: Session = Depends(get_session)):
    """
    Trigger a synchronization of risk tiers for all servers.

    The underlying logic reads the six risk axis scores from
    ``McpLlmAxisScore`` (joined with ``McpServerRegistry``),
    applies the ``trust_gating_override`` policy, writes the computed
    ``risk_tier`` back to ``McpServerRegistry`` via the write service,
    and returns a summary of the operation.

    Returns
    -------
    dict
        A dictionary containing:
        - ``servers_updated``: number of servers whose tier was written.
        - ``tiers``: mapping of tier name to count of servers in that tier.
    """
    return sync_risk_tiers(session)