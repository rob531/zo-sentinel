from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_attestation_expiry_summary


class ExpiryBuckets(BaseModel):
    expired: int
    expiring_7d: int
    expiring_30d: int
    valid: int
    no_expiry: int


class AttestationExpiryResponse(BaseModel):
    total: int
    servers_with_attestation: int
    servers_without_attestation: int
    expiry_buckets: ExpiryBuckets


router = APIRouter(prefix="/api")


@router.get(
    "/attestations/expiry",
    response_model=AttestationExpiryResponse,
)
def get_attestation_expiry(
    session: Session = Depends(get_session),
) -> AttestationExpiryResponse:
    return AttestationExpiryResponse(
        **get_attestation_expiry_summary(session)
    )


if __name__ == "__main__":
    sample = AttestationExpiryResponse(
        total=2,
        servers_with_attestation=2,
        servers_without_attestation=1,
        expiry_buckets={
            "expired": 1,
            "expiring_7d": 0,
            "expiring_30d": 0,
            "valid": 1,
            "no_expiry": 0,
        },
    )
    assert sample.total == 2
    assert sample.servers_without_attestation == 1
    assert sum(sample.expiry_buckets.model_dump().values()) == sample.total
    print("PASS")