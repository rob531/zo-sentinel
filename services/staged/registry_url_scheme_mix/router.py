"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import url_scheme_mix

router = APIRouter(prefix="/api", tags=["registry_url_scheme_mix"])


class SchemeInfo(BaseModel):
    scheme: str
    count: int
    share: float = Field(..., description="Proportion of total (0‑1)")
    non_https_by_source: dict[str, int] = Field(
        default_factory=dict,
        description="Counts per registry_source for non‑https schemes",
    )


class UrlSchemeMixResponse(BaseModel):
    total: int
    schemes: list[SchemeInfo]


@router.get(
    "/registry/url-scheme-mix",
    response_model=UrlSchemeMixResponse,
    summary="Group server registry URLs by scheme",
)
def get_url_scheme_mix(db: Session = Depends(get_session)) -> UrlSchemeMixResponse:
    """
    Retrieve a breakdown of server registry URLs by their URL scheme.

    The underlying logic aggregates rows from `mcp_server_registry`,
    determines the scheme (https, http, ws/wss, or unknown), counts
    occurrences, computes each scheme's share of the total, and for
    non‑https schemes provides a further breakdown by `registry_source`.
    """
    result = url_scheme_mix(db)

    # `result` is expected to be a dict with keys:
    #   total: int
    #   schemes: list[dict] where each dict matches SchemeInfo fields.
    # The logic layer already returns data in the correct shape, so we
    # simply pass it through to the response model.
    return UrlSchemeMixResponse(**result)