"""HTTP surface for organisation API-key and user hygiene reporting."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_key_hygiene as _get_org_key_hygiene

router = APIRouter(prefix="/api", tags=["org-key-hygiene"])


class OrgHygiene(BaseModel):
    org_id: str
    org_name: str
    users: int
    keys: int
    unlabelled_keys: int
    keys_older_than_days: int
    inactive_users: int
    findings: list[str]


class OrgHygieneTotals(BaseModel):
    orgs: int
    users: int
    keys: int
    findings: int


class OrgKeyHygieneResponse(BaseModel):
    days: int
    generated_at: datetime
    orgs: list[OrgHygiene]
    totals: OrgHygieneTotals


@router.get("/orgs/hygiene", response_model=OrgKeyHygieneResponse)
def get_org_key_hygiene_endpoint(
    days: int = Query(default=90, ge=1),
    db: Session = Depends(get_session),
) -> OrgKeyHygieneResponse:
    return _get_org_key_hygiene(db, days)