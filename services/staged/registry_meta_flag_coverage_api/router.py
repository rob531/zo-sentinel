from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_registry_meta_flag_coverage

router = APIRouter(
    prefix="/api",
    tags=["registry-meta-flag-coverage"],
)


class FlagCoverage(BaseModel):
    key: str
    servers_present: int
    coverage_pct: float
    tier_breakdown: Dict[str, int]


class MetaFlagCoverageResponse(BaseModel):
    total_servers: int
    servers_with_meta: int
    servers_without_meta: int
    mean_keys_per_server: float
    distinct_key_count: int
    flags: List[FlagCoverage]


@router.get(
    "/registry/meta-flag-coverage",
    response_model=MetaFlagCoverageResponse,
)
def registry_meta_flag_coverage(
    session: Session = Depends(get_session),
) -> MetaFlagCoverageResponse:
    return MetaFlagCoverageResponse(
        **get_registry_meta_flag_coverage(session)
    )