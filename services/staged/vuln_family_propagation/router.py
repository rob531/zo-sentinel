from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import propagate_vuln_family, PropagationResult

router = APIRouter(prefix="/api", tags=["vuln_family_propagation"])


@router.post(
    "/vuln/propagate",
    response_model=PropagationResult,
    summary="Propagate vulnerability families across server registries",
)
def propagate_endpoint(session: Session = Depends(get_session)):
    """
    Trigger propagation of CVE matches through server families.

    The underlying business logic reads `VulnLink` and `VulnAdvisory`,
    updates matches for servers belonging to families, and returns a
    summary of the operation.
    """
    return propagate_vuln_family(session)