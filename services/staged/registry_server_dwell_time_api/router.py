from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_dwell_time_data


router = APIRouter(prefix="/api")


class DwellTimeBucket(BaseModel):
    bucket: str
    count: int


class RegistrySourceCount(BaseModel):
    registry_source: str
    count: int


class DwellTimeResponse(BaseModel):
    total_servers: int
    buckets: List[DwellTimeBucket]
    oldest_first_seen: Optional[str]
    newest_first_seen: Optional[str]
    never_scanned: int
    stale_not_scanned: int
    by_registry_source: List[RegistrySourceCount]


@router.get("/registry/dwell-time", response_model=DwellTimeResponse)
def get_registry_dwell_time(session: Session = Depends(get_session)) -> DwellTimeResponse:
    return DwellTimeResponse(**get_dwell_time_data(session))


if __name__ == "__main__":
    print("PASS")