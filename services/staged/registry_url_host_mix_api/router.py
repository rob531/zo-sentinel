from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_url_host_mix as _get_url_host_mix


router = APIRouter(prefix="/api")


class UrlHostCount(BaseModel):
    host: str
    count: int
    sources: list[str]
    share: float


class UrlHostTotals(BaseModel):
    hosts: int
    urls: int


class UrlHostMixResponse(BaseModel):
    hosts: list[UrlHostCount]
    totals: UrlHostTotals


@router.get(
    "/registry/url-host-mix",
    response_model=UrlHostMixResponse,
    summary="List the most common URL hosts in the MCP server registry",
)
def registry_url_host_mix(
    limit: int = Query(default=50, ge=1),
    min_count: int | None = Query(default=None, ge=0),
    session=Depends(get_session),
) -> UrlHostMixResponse:
    result = _get_url_host_mix(
        session=session,
        limit=limit,
        min_count=min_count,
    )
    return UrlHostMixResponse(**result)


if __name__ == "__main__":
    sample = UrlHostMixResponse(
        hosts=[
            UrlHostCount(
                host="example.com",
                count=2,
                sources=["public"],
                share=1.0,
            )
        ],
        totals=UrlHostTotals(hosts=1, urls=2),
    )
    assert sample.totals.urls == 2
    assert sample.hosts[0].count == 2
    assert 0.0 <= sample.hosts[0].share <= 1.0
    print("PASS")