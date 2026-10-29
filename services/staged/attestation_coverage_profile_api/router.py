from fastapi import APIRouter, Depends
from app.db import get_session

from .logic import get_attestation_coverage_profile

router = APIRouter(prefix="/api")


@router.get("/attestations/coverage-profile")
def attestation_coverage_profile(session=Depends(get_session)):
    return get_attestation_coverage_profile(session)


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/attestations/coverage-profile"
        for route in router.routes
    )
    print("PASS")