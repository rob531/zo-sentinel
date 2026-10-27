from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_package_hotspots

router = APIRouter(prefix="/api", tags=["vulnerability package hotspots"])


class RecentAdvisory(BaseModel):
    id: int
    summary: str | None = None
    severity: str | None = None
    published_at: str | None = None


class PackageHotspot(BaseModel):
    package: str
    ecosystem: str | None = None
    advisory_count: int = Field(ge=0)
    max_severity: str
    linked_server_count: int = Field(ge=0)
    mean_match_confidence: float | None = None
    latest_published_at: str | None = None
    recent_advisories: list[RecentAdvisory] = Field(default_factory=list)


class PackageHotspotResponse(BaseModel):
    packages: list[PackageHotspot]
    total_packages: int = Field(ge=0)


@router.get("/vuln/packages", response_model=PackageHotspotResponse)
def list_package_hotspots(
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> PackageHotspotResponse:
    return get_package_hotspots(session, limit=limit)