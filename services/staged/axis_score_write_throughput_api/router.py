from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_axis_score_write_throughput

router = APIRouter(prefix="/api")


class HourlyWriteCount(BaseModel):
    bucket_hour: datetime
    rows: int = Field(ge=0)
    servers: int = Field(ge=0)


class AxisScoreWriteThroughputResponse(BaseModel):
    window_minutes: int = Field(ge=1, le=1440)
    rows_written: int = Field(ge=0)
    distinct_servers: int = Field(ge=0)
    rows_per_minute: float = Field(ge=0)
    distinct_adapters: int = Field(ge=0)
    hourly: list[HourlyWriteCount]


@router.get(
    "/axis/scores/write-throughput",
    response_model=AxisScoreWriteThroughputResponse,
)
def axis_score_write_throughput(
    minutes: int = Query(default=60, ge=1, le=1440),
    session: Session = Depends(get_session),
) -> AxisScoreWriteThroughputResponse:
    result = get_axis_score_write_throughput(session, minutes=minutes)
    return AxisScoreWriteThroughputResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")