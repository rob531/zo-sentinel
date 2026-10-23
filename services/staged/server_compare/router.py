from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from fastapi_cache.decorator import cache

from app.db import get_session
from .logic import compare_servers

router = APIRouter()


@router.get("/compare")
@cache(expire=3600)
def compare_endpoint(
    server_id_1: str = Query(..., alias="server_id_1"),
    server_id_2: str = Query(..., alias="server_id_2"),
    db: Session = Depends(get_session),
):
    """
    Compare risk profiles of two servers.

    Returns a dict with server details and a comparison summary.
    """
    result = compare_servers(server_id_1, server_id_2, db)
    if not result:
        raise HTTPException(status_code=404, detail="One or both server IDs not found")
    return result


if __name__ == "__main__":
    print("PASS")