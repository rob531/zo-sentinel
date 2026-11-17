from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_adapter_sha_consistency

router = APIRouter(
    prefix="/api",
    tags=["axis-adapter-sha-consistency"],
)


class AxisAdapterSHAConsistency(BaseModel):
    axis_name: str
    distinct_adapters: int
    dominant_adapter: str
    dominant_share: float
    split_scored_servers: int


class AdapterSHAConsistencyResponse(BaseModel):
    days: int
    axes: list[AxisAdapterSHAConsistency]
    max_distinct_adapters: int


@router.get(
    "/axes/adapter-sha-consistency",
    response_model=AdapterSHAConsistencyResponse,
)
def adapter_sha_consistency(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> AdapterSHAConsistencyResponse:
    return get_adapter_sha_consistency(session, days)


if __name__ == "__main__":
    sample = AdapterSHAConsistencyResponse(
        days=30,
        axes=[
            AxisAdapterSHAConsistency(
                axis_name="security",
                distinct_adapters=2,
                dominant_adapter="a" * 64,
                dominant_share=0.75,
                split_scored_servers=1,
            ),
            AxisAdapterSHAConsistency(
                axis_name="quality",
                distinct_adapters=2,
                dominant_adapter="b" * 64,
                dominant_share=0.5,
                split_scored_servers=0,
            ),
        ],
        max_distinct_adapters=2,
    )
    assert sample.max_distinct_adapters == 2
    assert all(axis.dominant_share <= 1.0 for axis in sample.axes)
    print("PASS")