from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_perspective_membership_analysis

router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/{perspective_id}/membership_analysis",
    name="perspective_membership_analysis",
)
def perspective_membership_analysis(
    perspective_id: int, session: Session = Depends(get_session)
):
    """
    Retrieve membership analysis for a given perspective.

    The heavy‑lifting is performed in `services.staged.perspective_membership_analysis.logic`.
    """
    return get_perspective_membership_analysis(perspective_id, session)