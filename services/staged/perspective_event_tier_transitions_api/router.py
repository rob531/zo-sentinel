from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import PerspectiveEvent
from .logic import get_tier_transitions

router = APIRouter(prefix="/api", tags=["perspective-event-tier-transitions"])


class TierTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int
    seen_count: int


class MostCommonTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int


class PerspectiveTierTransitionsResponse(BaseModel):
    perspective_id: str
    total_events: int
    transitions: list[TierTransition]
    most_common_transition: Optional[MostCommonTransition]


@router.get(
    "/perspectives/tier-transitions",
    response_model=PerspectiveTierTransitionsResponse,
)
def get_perspective_tier_transitions(
    perspective_id: str,
    session=Depends(get_session),
) -> PerspectiveTierTransitionsResponse:
    result = get_tier_transitions(session, perspective_id)
    return PerspectiveTierTransitionsResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        yield None

    that_app.dependency_overrides[get_session] = override_get_session
    print("PASS")