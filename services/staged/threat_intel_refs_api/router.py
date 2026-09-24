from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from app.db import get_session

router = APIRouter(prefix="/api", tags=["threat_intel_refs"])


class ThreatIntelResponse(BaseModel):
    indicator_type: str
    indicator_value: str
    pulse_name: str
    source: str
    source_url: str
    pulse_created: str
    is_aggregator: bool


@router.get("/servers/{server_id}/threat-intel", response_model=List[ThreatIntelResponse])
async def get_server_threat_intel(server_id: str, session=Depends(get_session)):
    from .logic import get_threat_intel_for_server
    return await get_threat_intel_for_server(server_id, session)