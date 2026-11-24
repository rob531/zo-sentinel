"""HTTP surface for attestation expiry-lead reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import expiry_lead_buckets

router = APIRouter(prefix="/api/attestations", tags=["attestations"])


class ExpiryBucket(BaseModel):
    bucket: str
    count: int
    server_ids: list[str]


class ExpiryLeadResponse(BaseModel):
    days: int
    buckets: list[ExpiryBucket]
    total: int


@router.get("/expiry-lead", response_model=ExpiryLeadResponse)
def get_expiry_lead(
    days: int = Query(30, ge=0, le=3650),
    db: Session = Depends(get_session),
) -> ExpiryLeadResponse:
    result = expiry_lead_buckets(db, days)
    return ExpiryLeadResponse(
        days=days,
        buckets=[ExpiryBucket(**bucket) for bucket in result["buckets"]],
        total=result["total"],
    )