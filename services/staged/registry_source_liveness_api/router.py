from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_source_liveness


router = APIRouter(prefix="/api")


class RegistrySourceServer(BaseModel):
    server_id: Optional[str] = None
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    scan_count: Optional[int] = None

    class Config:
        extra = "allow"


class RegistrySourceLiveness(BaseModel):
    registry_source: Optional[str] = None
    server_count: int = 0
    never_scanned_count: int = 0
    scanned_last_24h_count: int = 0
    oldest_last_seen: Optional[datetime] = None
    newest_last_seen: Optional[datetime] = None
    hours_since_newest_last_seen: Optional[float] = None
    distinct_risk_tiers: dict[str, int] = {}

    servers: list[RegistrySourceServer] = []
    least_recently_seen_servers: list[RegistrySourceServer] = []

    class Config:
        extra = "allow"


class RegistrySourceLivenessResponse(BaseModel):
    generated_at: datetime
    sources: list[RegistrySourceLiveness] = []


@router.get(
    "/registry/source-liveness",
    response_model=RegistrySourceLivenessResponse,
)
def get_registry_source_liveness_api(
    registry_source: Optional[str] = None,
    db: Any = Depends(get_session),
) -> RegistrySourceLivenessResponse:
    result = get_registry_source_liveness(db, registry_source=registry_source)

    if isinstance(result, dict):
        if "sources" in result:
            sources = result["sources"]
        elif "source_liveness" in result:
            sources = result["source_liveness"]
        elif "registry_source" in result:
            sources = [result]
        else:
            sources = []
    elif isinstance(result, (list, tuple)):
        sources = list(result)
    else:
        sources = []

    return RegistrySourceLivenessResponse(
        generated_at=datetime.now(timezone.utc),
        sources=sources,
    )