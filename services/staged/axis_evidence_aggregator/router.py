from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_evidence

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/axis-evidence")
def axis_evidence_endpoint(server_id: int, db: Session = Depends(get_session)):
    """
    Retrieve aggregated axis evidence for a given server.
    """
    return get_axis_evidence(server_id, db)