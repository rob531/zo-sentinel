from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_api_key_rotation_coverage

router = APIRouter(prefix="/api", tags=["api-key-rotation-coverage"])


class OrgKeyRotation(BaseModel):
    org_id: str
    org_name: str
    key_count: int
    oldest_created_at: str
    newest_created_at: str
    oldest_age_days: float
    stale_over_90_days: bool


class APIKeyRotationCoverageResponse(BaseModel):
    total_keys: int
    org_count: int
    rotation_days: int
    orgs: list[OrgKeyRotation]


@router.get("/keys/rotation", response_model=APIKeyRotationCoverageResponse)
def get_rotation_coverage(session: Session = Depends(get_session)):
    return get_api_key_rotation_coverage(session)