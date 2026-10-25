"""Registry URL scheme rollup API router."""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401

from .logic import get_url_scheme_rollup

router = APIRouter(prefix="/api")


class SchemeCount(BaseModel):
    scheme: str
    count: int
    pct: float


class RiskTierCount(BaseModel):
    risk_tier: Optional[str]
    count: int


class UrlSchemeRollupResponse(BaseModel):
    total: int
    by_scheme: List[SchemeCount]
    insecure_by_tier: List[RiskTierCount]
    insecure_total: int
    missing_url_total: int


@router.get(
    "/registry/url-scheme-rollup",
    response_model=UrlSchemeRollupResponse,
)
def get_registry_url_scheme_rollup(
    session: Session = Depends(get_session),
) -> UrlSchemeRollupResponse:
    return get_url_scheme_rollup(session)