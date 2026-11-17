from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_attestation_staleness


class Bucket(BaseModel):
    bucket: str
    count: int


class AttestationStalenessResponse(BaseModel):
    buckets: List[Bucket]
    total_attestations: int
    covered_servers: int
    servers_without_attestation: int
    stale_over_24h: int


router = APIRouter(prefix="/api", tags=["Attestation Staleness"])


@router.get(
    "/attestations/staleness",
    response_model=AttestationStalenessResponse,
    summary="Get attestation staleness statistics",
)
def attestation_staleness_endpoint(session: Session = Depends(get_session)):
    """
    Thin wrapper that delegates to the business logic layer.
    """
    return get_attestation_staleness(session)