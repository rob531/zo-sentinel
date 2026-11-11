from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_facet_filter_coverage


router = APIRouter(prefix="/api", tags=["perspective-facet-filter-coverage"])


class FilterKeyCount(BaseModel):
    key: str
    count: int


class PerspectiveCoverage(BaseModel):
    perspective_id: str
    name: str | None
    org_id: str | None
    has_filters: bool
    filter_keys: list[str]
    event_count: int
    distinct_servers: int
    unseen_events: int
    change_types: dict[str, int]


class FacetFilterCoverageResponse(BaseModel):
    total_perspectives: int
    with_filters: int
    without_filters: int
    filter_key_counts: list[FilterKeyCount]
    perspectives: list[PerspectiveCoverage]


@router.get(
    "/perspectives/facet-filter-coverage",
    response_model=FacetFilterCoverageResponse,
    summary="Report perspective facet-filter coverage",
)
def get_perspective_facet_filter_coverage(
    session: Session = Depends(get_session),
) -> FacetFilterCoverageResponse:
    return get_facet_filter_coverage(session)


if __name__ == "__main__":
    print("PASS")