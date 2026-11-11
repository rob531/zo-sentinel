from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from .logic import get_api_key_inventory_age


class ApiKeyAgeBucket(BaseModel):
    range: str
    min_days: int
    max_days: int | None
    count: int


class ApiKeyAgeByOrg(BaseModel):
    org_id: str
    count: int
    oldest_created_at: str | None


class ApiKeyInventoryAgeResponse(BaseModel):
    total: int
    buckets: list[ApiKeyAgeBucket]
    oldest_created_at: str | None
    by_org: list[ApiKeyAgeByOrg]
    stale_keys_90d: int


router = APIRouter(prefix="/api", tags=["api-keys"])


@router.get(
    "/api-keys/age",
    response_model=ApiKeyInventoryAgeResponse,
    response_model_exclude_none=False,
)
def api_key_inventory_age(session=Depends(get_session)):
    return get_api_key_inventory_age(session)