from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_api_key_usage_rollup

router = APIRouter(prefix="/api")


class OrgApiKeyUsage(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    oldest_key_at: str | None = None
    newest_key_at: str | None = None
    days_since_last_key: int | None = None


class OrgApiKeyUsageSummary(BaseModel):
    orgs: int
    keys: int
    orgs_without_keys: int


class OrgApiKeyUsageRollupResponse(BaseModel):
    orgs: list[OrgApiKeyUsage]
    summary: OrgApiKeyUsageSummary


@router.get(
    "/orgs/api-keys/usage-rollup",
    response_model=OrgApiKeyUsageRollupResponse,
)
def get_org_api_key_usage_rollup_endpoint(
    session: Session = Depends(get_session),
) -> OrgApiKeyUsageRollupResponse:
    result = get_org_api_key_usage_rollup(session)
    return OrgApiKeyUsageRollupResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")