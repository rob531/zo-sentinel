from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_unauth_access_posture_summary as _get_unauth_access_posture_summary


router = APIRouter(prefix="/api", tags=["unauth-access-posture"])


class UnauthAccessClassCount(BaseModel):
    class_: str = Field(alias="class")
    count: int

    class Config:
        allow_population_by_field_name = True


class UnauthAccessSourceCount(BaseModel):
    source: str
    count: int


class UnauthAccessRiskTierCount(BaseModel):
    risk_tier: str
    flagged_count: int


class UnauthAccessPostureSummaryResponse(BaseModel):
    total_servers: int
    by_class: List[UnauthAccessClassCount]
    by_source: List[UnauthAccessSourceCount]
    by_risk_tier: List[UnauthAccessRiskTierCount]
    generated_at: datetime


@router.get(
    "/unauth/posture/summary",
    response_model=UnauthAccessPostureSummaryResponse,
    response_model_by_alias=True,
)
def unauth_access_posture_summary(
    session=Depends(get_session),
) -> UnauthAccessPostureSummaryResponse:
    return _get_unauth_access_posture_summary(session)


if __name__ == "__main__":
    sample = UnauthAccessPostureSummaryResponse(
        total_servers=1,
        by_class=[{"class": "UNCONFIRMED", "count": 1}],
        by_source=[{"source": "contract", "count": 1}],
        by_risk_tier=[{"risk_tier": "HIGH_RISK_ISOLATED", "flagged_count": 1}],
        generated_at=datetime.utcnow(),
    )
    serialized = sample.dict(by_alias=True)
    assert serialized["by_class"][0]["class"] == "UNCONFIRMED"
    assert serialized["by_risk_tier"][0]["flagged_count"] == 1
    print("PASS")