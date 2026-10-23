from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from .logic import search_advisories_by_alias

router = APIRouter(prefix="/api", tags=["vuln-advisory-alias-index"])


class AdvisoryAliasMatch(BaseModel):
    advisory_id: int
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    matched_alias: str
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None


class AdvisoryAliasIndexResponse(BaseModel):
    count: int
    items: list[AdvisoryAliasMatch]


@router.get(
    "/vuln/advisories/aliases",
    response_model=AdvisoryAliasIndexResponse,
)
async def get_vuln_advisory_alias_index(
    ecosystem: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    q: str = Query(min_length=1),
    session: AsyncSession = Depends(get_session),
) -> AdvisoryAliasIndexResponse:
    result = await search_advisories_by_alias(
        session=session,
        ecosystem=ecosystem,
        severity=severity,
        q=q,
    )
    return AdvisoryAliasIndexResponse.model_validate(result)