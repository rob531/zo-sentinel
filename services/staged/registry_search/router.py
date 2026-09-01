from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import search_registry

router = APIRouter(prefix="/api")


@router.get("/registry/search")
def registry_search(
    q: str = Query(..., description="Search term"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of results"),
    session: Session = Depends(get_session),
):
    """
    Search the MCP server registry.

    Returns a JSON payload matching the Pydantic response schema:
    {
        "results": [
            {
                "server_id": int,
                "name": str,
                "url": str,
                "registry_source": str,
                "verdict": str,
                "risk_tier": str,
                "trust_score": float,
                "last_assessed": str,
                "last_seen": str,
            },
            ...
        ]
    }
    """
    try:
        return search_registry(session=session, q=q, limit=limit)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))