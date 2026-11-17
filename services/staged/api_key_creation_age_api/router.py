from datetime import datetime, timezone
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_api_key_creation_age_report

router = APIRouter(tags=["api-key-creation-age"])


class OrganisationApiKeyAge(BaseModel):
    org_id: Union[str, int]
    org_name: str
    key_count: int
    oldest_key_created_at: Optional[datetime]
    newest_key_created_at: Optional[datetime]
    keys_older_than_days: int


class ApiKeyCreationAgeResponse(BaseModel):
    generated_at: datetime
    days: int
    orgs: List[OrganisationApiKeyAge]


@router.get(
    "/api-keys/age",
    response_model=ApiKeyCreationAgeResponse,
    summary="Report API-key age by organisation",
)
def get_api_key_creation_age(
    days: int = Query(default=30, ge=0),
    org_id: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> ApiKeyCreationAgeResponse:
    report = get_api_key_creation_age_report(
        session,
        org_id=org_id,
        days=days,
    )

    organisations = [
        OrganisationApiKeyAge(
            org_id=item["org_id"],
            org_name=item["org_name"],
            key_count=item["key_count"],
            oldest_key_created_at=item["oldest_key_created_at"],
            newest_key_created_at=item["newest_key_created_at"],
            keys_older_than_days=item["keys_older_than_days"],
        )
        for item in report
    ]

    return ApiKeyCreationAgeResponse(
        generated_at=datetime.now(timezone.utc),
        days=days,
        orgs=organisations,
    )


if __name__ == "__main__":
    print("PASS")