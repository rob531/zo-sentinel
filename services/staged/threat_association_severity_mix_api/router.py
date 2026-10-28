from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_threat_association_severity_mix


class SeverityMixBand(BaseModel):
    band: Literal["1", "2-3", "4-10", "11+"]
    servers: int
    associations: int


class SeverityMixTopServer(BaseModel):
    server_id: str
    count: int


class ThreatAssociationSeverityMixResponse(BaseModel):
    total_associations: int
    server_count: int
    bands: list[SeverityMixBand]
    top_servers: list[SeverityMixTopServer]


router = APIRouter(prefix="/api")


@router.get(
    "/threat/association/severity-mix",
    response_model=ThreatAssociationSeverityMixResponse,
)
async def threat_association_severity_mix(
    limit: int = Query(default=20, ge=1, le=100),
    session: Any = Depends(get_session),
) -> ThreatAssociationSeverityMixResponse:
    result = await get_threat_association_severity_mix(session, limit=limit)
    return ThreatAssociationSeverityMixResponse.model_validate(result)


if __name__ == "__main__":
    assert router.prefix == "/api"
    route = next(
        route
        for route in router.routes
        if getattr(route, "path", None)
        == "/api/threat/association/severity-mix"
    )
    assert route.methods is not None
    assert "GET" in route.methods
    print("PASS")