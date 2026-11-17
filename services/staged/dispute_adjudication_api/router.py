from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import adjudicate_dispute, get_dispute

router = APIRouter(prefix="/api")


class AdjudicationRequest(BaseModel):
    decision: str = Field(pattern="^(ACCEPT|REJECT|DEFER)$")
    admin_note: str = Field(min_length=1)


@router.get("/disputes/{dispute_id}")
def read_dispute(
    dispute_id: int,
    session: Session = Depends(get_session),
):
    return get_dispute(session, dispute_id)


@router.post("/disputes/{dispute_id}/adjudicate")
def adjudicate(
    dispute_id: int,
    payload: AdjudicationRequest,
    session: Session = Depends(get_session),
):
    return adjudicate_dispute(
        session,
        dispute_id,
        payload.decision,
        payload.admin_note,
    )