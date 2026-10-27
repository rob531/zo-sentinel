from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_ecosystem_distribution


class EcosystemDistributionItem(BaseModel):
    ecosystem: str
    count: int
    critical: int
    high: int
    medium: int
    low: int
    newest_published_at: str | None = None
    oldest_published_at: str | None = None


class EcosystemDistributionResponse(BaseModel):
    total: int
    ecosystems: list[EcosystemDistributionItem]


router = APIRouter(prefix="/api", tags=["advisory-ecosystem-distribution"])


@router.get(
    "/vuln/advisories/ecosystems",
    response_model=EcosystemDistributionResponse,
)
def advisory_ecosystem_distribution(
    session: Session = Depends(get_session),
) -> EcosystemDistributionResponse:
    return get_ecosystem_distribution(session)


if __name__ == "__main__":
    route_paths = {route.path for route in router.routes}
    assert "/api/vuln/advisories/ecosystems" in route_paths
    assert EcosystemDistributionResponse(
        total=1,
        ecosystems=[
            EcosystemDistributionItem(
                ecosystem="unknown",
                count=1,
                critical=0,
                high=0,
                medium=1,
                low=0,
            )
        ],
    ).total == 1
    print("PASS")