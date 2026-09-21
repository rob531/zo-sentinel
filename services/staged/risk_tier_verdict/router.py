from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_verdict, VerdictResponse

router = APIRouter(prefix="/api")


@router.get("/verdict/{server_id}", response_model=VerdictResponse)
def get_verdict(
    server_id: int,
    session: Session = Depends(get_session),
):
    """
    Retrieve the risk‑tier verdict for a given server.
    """
    try:
        return get_risk_tier_verdict(server_id, session)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))