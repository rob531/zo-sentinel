from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import compute_membership_delta
from .contract import MembershipDeltaResponse

router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/{perspective_id}/membership-delta",
    response_model=MembershipDeltaResponse,
    name="Get Membership Delta",
)
def get_membership_delta_endpoint(
    perspective_id: str,
    session: Session = Depends(get_session),
) -> MembershipDeltaResponse:
    """
    Retrieve the membership delta for a given perspective.

    Parameters
    ----------
    perspective_id: str
        The identifier of the perspective.
    session: Session
        The SQLAlchemy session provided by the FastAPI dependency.

    Returns
    -------
    MembershipDeltaResponse
        The computed membership delta information.
    """
    try:
        result = compute_membership_delta(perspective_id, session)
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(exc))