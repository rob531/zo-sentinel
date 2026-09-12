from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

from .logic import consume_and_update_tiers

router = APIRouter()


class TierUpdate(BaseModel):
    server_id: str
    old_tier: str
    new_tier: str


class TierConsumeResponse(BaseModel):
    consumed: int
    updated: list[TierUpdate]


@router.get("/scoring/tier-consume", response_model=TierConsumeResponse)
def tier_consume_endpoint(session: Session = Depends(get_session)):
    return consume_and_update_tiers(session)