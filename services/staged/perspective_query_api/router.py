from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_perspective_servers

router = APIRouter(prefix="/api")


@router.get("/perspectives/{perspective_id}/servers")
def perspective_servers(perspective_id: int, db: Session = Depends(get_session)):
    """
    Retrieve servers associated with a perspective, applying the perspective's facet filters
    and returning server risk tier information.

    Returns a JSON structure:
    {
        "perspective": {
            "id": int,
            "name": str,
            "description": str,
            "filters": dict
        },
        "servers": [
            {
                "server_id": int,
                "name": str,
                "risk_tier": str,
                "change_type": str,
                "change_date": str
            },
            ...
        ]
    }
    """
    return get_perspective_servers(perspective_id, db)


__all__ = ["router"]