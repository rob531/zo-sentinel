from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from app.db import get_session
from .logic import get_override_for_server

router = APIRouter(prefix="/api", tags=["trust"])

class OverrideResponse(BaseModel):
    server_id: str
    name: str
    url: str
    has_override: bool
    override_tier: Optional[str]
    override_reason: Optional[str]

@router.get("/trust/override/{server_id}", response_model=OverrideResponse)
async def get_override(server_id: str, session=Depends(get_session)) -> OverrideResponse:
    return await get_override_for_server(session, server_id)